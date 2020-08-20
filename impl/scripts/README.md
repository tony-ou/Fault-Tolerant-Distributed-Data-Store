Testing configuration and chistributed command: 

Test Description 
------------

- test1: 
get & set functionality: Normal Redirect + get + set w/o failures + replication (e.g. read node-2 after setting node-1)

- test2: 
fault tolerance: fail a node, and get and set on that node should return error 

- test3: 
fail-stop resilient: get, set, reelection should work properly after leader/follower failures (with living nodes >= majority)

- test4:
Partition fail: parition into groups without majority, get & set should timeout on  

- test5:
Parition resilient: into groups with one with majority, get & set should work

Running Our tests
------------

Running all tests: 
```{bash}
./run_test
```

Running a single test:
```{bash}
timeout 40 chistributed --pub-port 15151 --router-port 56152 --show-node-output < scripts/chi/new_test1.chi
```

Note
------------
1. It seems that `quit` function is not working and we have communicated with other 
groups and TA: we decided to use a GNU command `timeout` to terminate the bash script
if it fails to stop. 
2. We also provide `kill.sh` script to kill all chistributed processes. 