ps -ef | grep 'chistributed' | grep -v grep | awk '{print $2}' | xargs -r kill -9
ps -ef | grep 'python' | grep -v grep | awk '{print $2}' | xargs -r kill -9