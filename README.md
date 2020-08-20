Raft Made Live:  A Simple Python Implementation
=============================================

CMSC 23310 Advanced Distributed System Final Project

Xifan Yu, xifan@uchicago.edu

Yiyang(Tony) Ou, yiyangou@uchicago.edu

Zixuan(Alex) Zhao, zhaozixuan@uchicago.edu

General Structure (Code Organization)
----------
Our main code is saved in the `node.py` file under `Impl`. In the `node` class, we implemented handlers for various custom messages, including:
- appendEntries / appendEntriesResponse
- requestVote / requestVoteResponse
- redirectSet / redirectGet / redirectGetResponse / redirectSetResponse
- get / set
- hello

For simplicity, when handler is called, it redistributes the work to handler functions that handle each message types correspondingly. We used the `threading` library for timeout and message resend mechanisms.

Under `paper` is our Latex and PDF of our written report. 

Test Cases
----------
We provided chistributed scripts and confiugrations under `impl/scripts` that included all various types of test cases, including get & set functionalities, fault tolerance and partition failuress, etc. Please refer to a detailed description of test cases @ `README` under `impl/scripts`. 

To run the test cases, you may run a single test case with
```{bash}
chistributed --pub-port 50000 --router-port 50001 < scripts/chi/new_test1.chi
```
We also provide a bash script `Impl/run_test.sh` that runs a batch of test case multiple times. To run `run_test.sh`, modify test case variable and port number to the test case that you want to test for. The shell script would copy the configurations and run the chistributed commands under `scripts` accordingly. 

```{bash}
./run_test.sh
```

We also provide our sample output under `scripts` that we have run our code on prior to submission. Detailed analysis of performance and correctness would be provided in our writeup PDF. 

Distribution of Work 
----------
- Xifan: Implemented leader selection, heartbeat mechanism and appendEntries with no logs cases.  
- Yiyang: Implemented log replication mechanism, dealt with timers, developed test cases. 
- Zixuan: Implemented log replication mechanism, developed test cases and testing scripts. 

We all debugged a lot together over Zoom :( 




