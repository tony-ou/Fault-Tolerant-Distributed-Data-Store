export PATH=~/.local/bin:$PATH

pport=23333
rport=46666
tot_test=5
./kill.sh

if [ ! -d "./scripts/output" ]; then
    mkdir ./scripts/output
fi 

for test_case in $(seq 1 $tot_test); do
    cp ./scripts/conf/test$test_case.conf ./chistributed.conf
    echo "Running test case $test_case"
    test_file=./scripts/chi/test$test_case.chi
    output_file=./scripts/output/test_output$test_case.txt
    echo "stored at: "$output_file
    timeout 40 chistributed --pub-port $pport --router-port $rport < $test_file > $output_file
done

# Run single test case
# timeout 40 chistributed --pub-port 15151 --router-port 56152 --show-node-output < scripts/chi/new_test1.chi