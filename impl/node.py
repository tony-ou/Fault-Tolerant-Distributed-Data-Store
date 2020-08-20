import json
import sys
import signal
import zmq
import time
import click
import time
import threading
import random

import colorama
colorama.init()

from zmq.eventloop import ioloop, zmqstream
ioloop.install()

random.seed(time.time())
            
election_timeout = 1
random_scale = 3
request_timeout = 5
round_trip_timeout= 1.5

def log_entry(term, key, value, msg_id):
    """ log entry
    Construct a log entry based on message 
    Return: log entry as dictionary
    """
    return {'term': int(term), 'key': key, 'value': value, 'id': msg_id}

def random_election_timeout():
    """ random election timeout 
    Return: a random number for election timeout 
    """
    return random.uniform(election_timeout, random_scale * election_timeout)

def check_vote(role):
    """ request votes
    Check the result of RequestVote RPC
    Return: True if success, false if not 
    """
    return role == "Leader" or role == "Follower"

# Represent a node in our data store
class Node(object):
    def __init__(self, node_name, pub_endpoint, router_endpoint, peer_names, debug):
        self.loop = ioloop.IOLoop.instance()
        self.context = zmq.Context()

        self.connected = False

        # SUB socket for receiving messages from the broker
        self.sub_sock = self.context.socket(zmq.SUB)
        self.sub_sock.connect(pub_endpoint)

        # Make sure we get messages meant for us!
        self.sub_sock.setsockopt_string(zmq.SUBSCRIBE, node_name)

        # Create handler for SUB socket
        self.sub = zmqstream.ZMQStream(self.sub_sock, self.loop)
        self.sub.on_recv(self.handle)

        # REQ socket for sending messages to the broker
        self.req_sock = self.context.socket(zmq.REQ)
        self.req_sock.connect(router_endpoint)
        self.req_sock.setsockopt_string(zmq.IDENTITY, node_name)

        # We don't strictly need a message handler for the REQ socket,
        # but we define one in case we ever receive any errors through it.
        self.req = zmqstream.ZMQStream(self.req_sock, self.loop)
        self.req.on_recv(self.handle_broker_message)

        self.name = node_name
        self.peer_names = peer_names

        self.debug = debug

        # This node's data store
        self.store = {}

        # Capture signals to ensure an orderly shutdown
        for sig in [signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT]:
            signal.signal(sig, self.shutdown)

        self.role = "Follower"

        # Persistent state on all servers
        self.current_term = 0

        self.voted_for = dict()

        self.logs = []

        # Volatile state on all servers
        self.commit_index = -1

        self.last_applied = -1

        # Volatile state on leaders
        self.next_index = {}
        for peer_name in peer_names:
            self.next_index[peer_name] = 0
        self.match_index = {}

        # calculate majority number of nodes
        self.majority = (len(self.peer_names) + 1) // 2 + 1

        # store leader information to redirect
        self.leader = None

        # election information
        self.votes = 0

        # store if leader is detected
        self.leader_detected = False

        # redirect_timer
        self.redirect_timers = {}

        # log replicaiton timers
        self.log_replication_timers = {}

        # get round trip timers
        self.get_round_trip_timers = {}

        # threads
        self.threads = []

        # round_trip get
        self.round_trip_replied = {}



    def getOperation(self, msg):
        """ Execute Get Operation 
        Get a value from the data store
        Send an error if there is no such key
        Return: None 
        """ 

        src = msg["source"] if "source" in msg else self.name
        k = msg['key']
        if k in self.store:
            v = self.store[k]
            self.send_to_broker({
                'source': src,
                'type': 'getResponse', 
                'id': msg['id'], 
                'key': k, 
                'value': v
            })
        else:
            self.send_to_broker({
                'source': src,
                'type': 'getResponse', 
                'id': msg['id'], 
                'error': "No such key: %s" % k
            })

        return None 
    
    def setOperation(self, msg):
        """ Execute Set Operation 
        Create new log entry and ask its follower to append entries
        Return: None 
        """ 
        new_log = log_entry(
            self.current_term, 
            msg['key'], 
            msg['value'], 
            msg['id']
        )

        self.logs.append(new_log)

        for peer_name in self.peer_names:
            self.append_entries_has_log(peer_name)
        return None 

    def appendEntriesResponseHandler(self, msg):
        """ Handle appendEntries Response
        Upon receiving appendEntries Response, update match index and 
        commit index if success. Otherwise, retry indefinitely 
        Return: None 
        """ 
        
        # revert to follower state if older term
        if msg['term'] > self.current_term:
            current_term = msg['term']
            self.role = 'Follower'
        else:
            # increment matchindex,nextindex if appendEntries succeed
            # decrease nextindex and try again if appendEntries fails
            if msg['has_log'] == True:
                if msg['flag'] == True:
                    self.match_index[msg['source']] = msg['match_index']
                    self.next_index[msg['source']] = msg['next_index']
                    self.update_commit()
                else:
                    self.next_index[msg['source']] -= 1
                    self.append_entries_has_log(msg['source'])

    def appendEntriesHandler(self, msg):
        """ Handle appendEntries 
        Both heartbeats and appendEntries are handled in this handler. 
        Log replication: 
        Send a success message to broker that includes updated next index, etc.
        Send a fail message if logs are not eligible to apply

        Heartbeat: acknolwedge that leaders have appeared and update node's states
        Return: None 
        """ 
        if msg['has_log'] == True:
            flag = True
            prevLogIndex = int(msg['prevLogIndex'])
            match_index = 0
            # check if leader's term is behind current term
            if int(msg['term']) < self.current_term:
                flag = False

            # check if prevLogindex matched
            if (prevLogIndex >= len(self.logs)) \
               or (prevLogIndex >= 0 \
                    and self.logs[prevLogIndex]['term'] != msg['prevLogTerm']):
                flag = False

            # if both criterion pass, we append entries 
            # sent from leader and update commit index
            if flag:
                entries = msg['entries']
                self.logs = self.logs[: prevLogIndex + 1] + entries
                old_commit_index = self.commit_index
                
                self.commit_index = max(
                    self.commit_index, 
                    min(len(self.logs) - 1, msg['leaderCommit'])
                )
                self.apply(self.last_applied + 1, self.commit_index)
                match_index = len(self.logs) - 1
            else:
                match_index = -1
                
            self.send_to_broker({
                'type': 'appendEntriesResponse', 
                'flag': flag, 
                'destination': msg['source'], 
                'has_log': True,
                'term': self.current_term,
                'match_index': match_index,
                'next_index': len(self.logs),
                'source':self.name
            })
        else:
            # heartbeats
            leader = msg["source"]
            if self.role == "Candidate":
                term = int(msg['term'])
                if self.current_term <= term:
                    self.role = "Follower"
                    self.leader_detected = True
                    self.leader = leader
                else:
                    self.send_to_broker({
                        'source': self.name,
                        'type': 'appendEntriesResponse',
                        'destination': msg['source'], 
                        'has_log': False,
                        'term': self.current_term, 
                        'match_index': self.commit_index, 
                        'next_index': len(self.logs)
                    })
            elif self.role == "Follower":
                self.leader_detected = True
                self.leader = leader

    def requestVoteResponseHandler(self, msg):
        """ Handle requestVoteResponse 
        Count the number of votes, become leader when a majority of nodes 
        respond to my requestVotes
        Return: None 
        """ 
        self.log("Receiving Vote Responses")
        if self.role != "Candidate":
            return None
        
        vote = msg['vote']
        if vote == True and self.current_term == msg['term']:
            self.votes += 1
            if self.votes >= self.majority:
                self.role = "Leader"
                # intialize match_index and next_index for leader
                for peer_name in self.peer_names:
                    self.match_index[peer_name] = -1
                    self.next_index[peer_name] = len(self.logs)
                self.append_entries()

                p = threading.Timer(0.1, self.heart_beat_loop)
                p.daemon = True
                p.start()
                # commit a dummy log for uncommited logs in previous terms
                self.redirectLeaderSetHandler(None, dummy_commit = True)
    
    def requestVoteHandler(self, msg):
        """ Handle requestVote
        See if the node sending requestVote is eligible to become a leader 
        if so, send requestResponse back
        Return: None 
        """ 
        if self.role != 'Leader':
            self.log("Receiving Vote Requests")
            term = int(msg['term'])
            candidate = msg['source']
            candidate_log_index = int(msg['lastLogIndex'])
            candidate_log_term = int(msg['lastLogTerm'])

            log_index = len(self.logs) - 1
            if log_index == -1:
                log_term = -1
            else:
                log_term = self.logs[log_index]['term']

            if term < self.current_term:
                self.send_to_broker({
                    'term':term, 
                    'source': self.name,
                    'type': 'requestVoteResponse',
                    'vote': False, 
                    'destination': candidate
                })
            elif (self.current_term not in self.voted_for \
                or self.voted_for[self.current_term] == candidate) \
                and (log_index <= candidate_log_index \
                    and log_term <= candidate_log_term):
                self.voted_for[self.current_term] = candidate
                self.send_to_broker({
                    'term': term, 
                    'source': self.name,
                    'type': 'requestVoteResponse', 
                    'vote': True,
                    'destination': candidate
                })
            else:
                self.send_to_broker({
                    'term': term, 
                    'source': self.name,
                    'type': 'requestVoteResponse', 
                    'vote': False,
                    'destination': candidate
                })

    def redirectLeaderSetHandler(self, msg, dummy_commit = False):
        """ Handle redirectLeaderSet
        Execute the setOperation. If the message is redirected from a follower,
        let the follower know that I have exectued this operation 
        Return: None 
        """ 
        if not dummy_commit:
            self.setOperation(msg)
            if msg["type"] == "redirectSet":
                self.send_to_broker({
                    "type" : "redirectSetResponse", 
                    "destination" : msg["source"], 
                    "source" : self.name,
                    "id": msg["id"] 
                })

            self.log_replication_timers[msg["id"]] = threading.Timer(
                request_timeout,
                self.request_error,
                args=['set', 'leader cant reach majority agreement', msg]
            )

            self.log_replication_timers[msg["id"]].start()
            self.threads.append(self.log_replication_timers[msg["id"]])
        else:
            self.setOperation({
                "key" : "dummy",
                "value" : -1,
                "id" : -1
             })


    def redirectLeaderGetHandler(self, msg):
        """ Handle redirectLeaderGet
        start a round trip to check if the leader is stale before processing get request. 
        If the message is redirected from a follower,
        let the follower know that I have exectued this operation 
        Return: None 
        """ 
        self.get_round_trip_timers[msg["id"]] = threading.Timer(
            round_trip_timeout,
            self.request_error,
            args=['get', 'leader stale', msg]
        )
        self.get_round_trip_timers[msg["id"]].start()
        self.threads.append(self.get_round_trip_timers[msg["id"]])
        self.round_trip_replied[msg['id']] = 1
        self.roundTrip(msg)
        
        if msg["type"] == "redirectGet":
            self.send_to_broker({
                "type" : "redirectGetResponse", 
                "destination" : msg["source"], 
                "source" : self.name,
                "id": msg["id"] 
            })
    
    def helloHandler(self, msg_frames):
        """ Handle helloHandler
        Connect the current node, start leader detection timer 
        Return: None 
        """ 
        if not self.connected:
            # Send helloResponse
            self.connected = True
            self.name = msg_frames[0] #save name of this node
            self.send_to_broker({'type': 'helloResponse', 'source': self.name})
            self.log("Node is running")

            # Start detection leader timer
            p = threading.Timer(0.1, self.leader_detection_loop)
            p.daemon = True
            p.start()

    def getFollowerHandler(self, msg):
        """ Handle getFollower
        If there is a leader, redirect the message using custom message redirect
        to the leader. Set a timer for response timeout.
        Return: None 
        """ 
        if self.leader == None:
            self.send_to_broker({
                'source': self.name,
                'type': "getResponse", 
                'id': msg['id'], 
                'error': "Get request error because leader does not exist."
            })
            return None 

        self.send_to_broker({
            "type" : "redirectGet", 
            "destination" : self.leader, 
            "source" : self.name,
            "key" : msg["key"],
            "id" : msg["id"]
        })

        self.redirect_timers[msg["id"]] = threading.Timer(
            request_timeout,
            self.request_error,
            args=['get', 'leader timeout', msg]
        )
        self.redirect_timers[msg["id"]].start()
        self.threads.append(self.redirect_timers[msg["id"]])

    def setFollowerHandler(self, msg):
        """ Handle setFollower
        If there is a leader, redirect the message using custom message redirect
        to the leader. Set a timer for response timeout.
        Return: None 
        """ 
        # redirect to leader
        if self.leader == None:
            self.send_to_broker({
                'source': self.name,
                'type': "setResponse", 
                'id': msg['id'], 
                'error': "Set request error because leader does not exist."
            })
            return None 

        self.send_to_broker({
            "type" : "redirectSet", 
            "destination" : self.leader, 
            "source" : self.name,
            "key" : msg["key"],
            "value" : msg["value"],
            "id" : msg["id"]
        })

        self.redirect_timers[msg["id"]] = threading.Timer(
            request_timeout, 
            self.request_error,
            args=['set', 'leader timeout', msg]
        )

        self.redirect_timers[msg["id"]].start()
        self.threads.append(self.redirect_timers[msg["id"]])
        
    def roundTripResponseHandler(self,msg):
        """ Handle round trip response 
        check if more than majority still thinks I am a leader 
        if yes perform getOperation and stop timer 
        Return: None 
        """ 
        if msg['id'] in self.round_trip_replied:
            self.round_trip_replied[msg['id']] +=1
            if self.round_trip_replied[msg['id']] >= self.majority:
                self.get_round_trip_timers[msg['id']].cancel()
                self.getOperation(msg)
                del self.round_trip_replied[msg['id']]

    def roundTripHandler(self,msg):
        """ Handle round trip 
        respond if the sender is this node's leader
        Return: None 
        """ 
        if self.leader == msg['source']:
            self.send_to_broker({
                'source': self.name,
                'destination': msg['source'],
                'type': 'roundTripResponse',
                'key': msg['key'], 
                'id': msg['id']
            })

    # Logging functions
    def log(self, msg):
        log_msg = ">>> %10s -- %s" % (self.name, msg)
        print(colorama.Style.BRIGHT + log_msg + colorama.Style.RESET_ALL)

    def log_debug(self, msg):
        if self.debug:
            log_msg = ">>> %10s -- %s" % (self.name, msg)
            print(colorama.Fore.BLUE + log_msg + colorama.Style.RESET_ALL)

    # Starts the ZeroMQ loop
    def start(self):
        self.loop.start()

    # Handle replies received through the REQ socket. Typically,
    # this will just be an acknowledgement of the message sent to the
    # broker through the REQ socket, but can also be an error message.
    def handle_broker_message(self, msg_frames):
        # TODO: Check whether there is an error
        pass


    # Sends a message to the broker
    def send_to_broker(self, d):
        self.req.send_json(d)
        self.log_debug("Sent: %s" % d) 

    # Handles messages received from the broker (which can originate in
    # other nodes or in the broker itself)
    def handle(self, msg_frames):
        """ Handle messages
        Redistribute work to other handlers depending on its message type 
        Return: None 
        """ 
        # Unpack the message frames.
        # in the event of a mismatch, format a nice string with msg_frames in
        # the raw, for debug purposes
        assert len(msg_frames) == 3, ((
            "Multipart ZMQ message had wrong length. "
            "Full message contents:\n{}").format(msg_frames))
        assert msg_frames[0] == self.name
        # Second field is the empty delimiter
        msg = json.loads(msg_frames[2])

        self.log_debug("Received " + str(msg_frames))

        # Recieved other types of messages before connected
        if self.connected == False and msg["type"] != "hello":
            return None 
        
        if msg['type'] == 'appendEntriesResponse':
            self.appendEntriesResponseHandler(msg)
        elif msg['type'] == 'appendEntries':
            self.appendEntriesHandler(msg)
        elif msg['type'] == 'requestVoteResponse':
            self.requestVoteResponseHandler(msg)
        elif msg['type'] == 'requestVote':
            self.requestVoteHandler(msg)
        elif msg["type"] == "redirectSet" or \
             (msg["type"] == "set" and self.role == "Leader"):
            self.redirectLeaderSetHandler(msg)
        elif msg["type"] == "redirectGet" or \
            (msg["type"] == "get" and self.role == "Leader"):
            self.redirectLeaderGetHandler(msg)
        elif msg["type"] == "redirectGetResponse" or \
             msg["type"] == "redirectSetResponse":
            self.redirect_timers[msg["id"]].cancel()
        elif msg['type'] == 'get' and self.role != "Leader":
            self.getFollowerHandler(msg)
        elif msg['type'] == 'set' and self.role != "Leader":
            self.setFollowerHandler(msg)
        elif msg['type'] == 'roundTripResponse':
            self.roundTripResponseHandler(msg)
        elif msg['type'] == 'roundTrip':
            self.roundTripHandler(msg)
        elif msg['type'] == 'hello':
           self.helloHandler(msg_frames)
        else:
            self.log("Received unknown message type: %s" % msg['type'])
    


    def roundTrip(self,msg):
        """ Round trip
        send to each node in the quorum a round trip message
        Return: None 
        """ 

        for p in self.peer_names:
            self.send_to_broker({
                'source': self.name,
                'destination': p,
                'type': 'roundTrip',
                'key': msg['key'], 
                'id': msg['id'],
            })

    def request_error(self, err_type, err, msg):
        """ Request error 
        send an error message to the broker 
        Return: None 
        """ 

        # if this is error from round trip, revert to follower
        if err == 'leader stale':
            self.role = 'Follower'
        self.send_to_broker({
            'source': self.name,
            'type': err_type + 'Response', 
            'id': msg['id'], 
            'error': "{} request error because {}".format(err_type, err)
        })

    def heart_beat_loop(self):
        """ heart beat loop 
        Sending constant heartbeats 
        Return: None 
        """ 
        self.log('hearbeat')
        while self.role == "Leader":
            # send heartbeats
            self.append_entries()
            time.sleep(0.1)
        self.log("heartbeat stops")


    def leader_detection_loop(self):
        """ leader detection loop
        Loop that checks for presence of a leader
        Return: None
        """ 
        while (self.role != None):
            self.leader_detected = False
            time.sleep(random_election_timeout())
            self.check_heart_beat()
        self.log("Leader detection stops")


    def check_heart_beat(self):
        """ check_heart_beat
        Check if heartbeat was received, or requestVote Otherwise
        Return: None
        """ 
        if self.role != "Follower":
            return None
        if self.leader_detected == False:
            self.request_vote()

    def request_vote(self):
        """ request votes
        If no heartbeat, request votes to other nodes
        Return None 
        """
        self.leader = None

        while True:
            self.current_term += 1
            self.role = "Candidate"
            self.votes = 1

            log_index = len(self.logs) - 1
            if log_index == -1:
                log_term = -1
            else:
                log_term = self.logs[log_index]['term']
            for peer_name in self.peer_names:
                self.send_to_broker({
                    'type': 'requestVote', 
                    'source': self.name,
                    'destination': peer_name, 
                    'term': self.current_term,
                    'lastLogIndex': log_index, 
                    'lastLogTerm': log_term
                })
                
            time.sleep(random_election_timeout())
            if check_vote(self.role):
                break
        return None

    def append_entries(self):
        """ append_entries without logs 
        Heartbeats using appendEntries RPC without logs 
        Return: None
        """
        log_index = len(self.logs) - 1
        for peer_name in self.peer_names:
            next_index = self.next_index[peer_name]
            if next_index == 0:
                prevLogTerm = -1
            else:
                prevLogTerm = self.logs[next_index - 1]['term']

            self.send_to_broker({
                'type': 'appendEntries', 
                'source': self.name,
                'destination': peer_name, 
                'term': self.current_term,
                'prevLogIndex': next_index - 1,
                'prevLogTerm': prevLogTerm,
                'has_log': False
            })
        return None

    def append_entries_has_log(self, peer_name):
        """ append_entries with logs 
        Send appendEntries RPC with logs 
        Return: None
        """
        log_index = len(self.logs) - 1
        next_index = self.next_index[peer_name]
        if next_index == 0:
            prevLogTerm = -1
        else:
            prevLogTerm = self.logs[next_index - 1]['term']

        if log_index >= next_index:
            self.send_to_broker({
                'type': 'appendEntries',
                'source': self.name, 
                'destination': peer_name, 
                'term': self.current_term, 
                'prevLogIndex': next_index - 1,
                'prevLogTerm': prevLogTerm, 
                'has_log': True, 
                'entries': self.logs[next_index:],
                'leaderCommit': self.commit_index
            })

        return None

    def update_commit(self):
        """ update commit 
        Update commit_index for leader, checking if match_index passed majority
        return None 
        """
        for log_index in range(len(self.logs) - 1, self.commit_index, -1):
        
            matched = 1
            for peer_name in self.peer_names:
                matched += (self.match_index[peer_name] >= log_index)
            if matched >= self.majority:
                self.apply(self.commit_index + 1, log_index)

                # respond to set request and cancel log_replication timer
                for new_rep_index in range(self.commit_index+1, log_index + 1):
                    log = self.logs[new_rep_index]
                    if log['id'] in self.log_replication_timers:
                        self.log_replication_timers[log['id']].cancel()
                        self.send_to_broker({
                            'source':self.name,
                            'type': 'setResponse', 
                            'id': log['id'], 
                            'key': log['key'], 
                            'value': log['value']
                        })
                self.commit_index = log_index
                
                break

    def apply(self, start, end):
        """ apply 
        Apply log to data store within [start, end] range
        Return: None 
        """
        for i in range(start, end + 1):
            curr_log = self.logs[i]
            k, v, msg_id = curr_log['key'], curr_log['value'], curr_log['id']
            self.store[k] = v
        self.last_applied = end

    # Performs an orderly shutdown
    def shutdown(self, sig, frame):
        """ shutdown
        check Timers, join threads, shutdown
        Return: None 
        """
        self.role = None
        for pthread in self.threads:
            pthread.join()

        self.loop.stop()
        self.sub_sock.close()
        self.req_sock.close()
        self.log("shutdown")

        sys.exit(0)


# Command-line parameters
@click.command()
@click.option('--pub-endpoint', type=str, default='tcp://127.0.0.1:23310')
@click.option('--router-endpoint', type=str, default='tcp://127.0.0.1:23311')
@click.option('--node-name', type=str)
@click.option('--peer', multiple=True)
@click.option('--debug', is_flag=True)
def run(pub_endpoint, router_endpoint, node_name, peer, debug):

    # Create a node and run it
    n = Node(node_name, pub_endpoint, router_endpoint, peer, debug)

    n.start()

if __name__ == '__main__':
    run()
