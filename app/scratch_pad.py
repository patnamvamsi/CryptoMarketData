import collections
import sys, os
#sys.path.insert(1, os.getcwd()+"/../")
import db.timescaledb.crud as q
import  datetime
from kafka import KafkaProducer
from kafka import KafkaConsumer
import time


#producer = KafkaProducer(bootstrap_servers='172.17.0.3:9092', api_version=(2,8,1))
producer = KafkaProducer(bootstrap_servers=['172.17.0.3:9092'])
for _ in range(10):
    msg = 'loop '+str(_)
    producer.send('vamsi_test', bytes(msg.encode('utf-8')))
    print("Sent msg {}".format(msg))
    time.sleep(1)


'''
consumer = KafkaConsumer('vamsi_test',bootstrap_servers='localhost:9092')
for msg in consumer:
    print(msg)
'''


consumer = KafkaConsumer(
    'vamsi_test',
     bootstrap_servers='172.17.0.3:9092',
     auto_offset_reset='earliest',
     enable_auto_commit=True,
    max_poll_records=100,
)



for message in consumer:
     print(message.value)


'''
sample = """2014-08-04 09:20:03 Summary of events: messages=421, rejections=2, drops=10, orders=98, trades=5
            2014-08-04 09:22:08 Summary of events: messages=3862, trades=22, rejections=1, drops=3, orders=158      
            2014-08-04 09:23:01 Summary of events: messages=39, drops=1, orders=20, trades=4, rejections=2"""

final_count =0
for lines in sample:
    trades_regex = re.search("trades=\d+", sample)
    count  = sample[trades_regex.start():trades_regex.end()].split('=', 1)[1]
    final_count = final_count + count


def create_staircase(nums):
  while len(nums) != 0:
    step = 1
    subsets = []
    if len(nums) >= step:
      subsets.append(nums[0:step])
      nums = nums[step:]
      step += 1
    else:
      return False

  return subsets

print (create_staircase([1,2,3,4,5,6]))

'''