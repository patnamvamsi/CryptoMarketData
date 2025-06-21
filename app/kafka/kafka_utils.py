from kafka import KafkaProducer
from kafka.admin import KafkaAdminClient, NewTopic
from kafka.errors import TopicAlreadyExistsError
from app.config.config import KAFKA_TOPICS, KAFKA_BOOTSTRAP_SERVERS, STREAM_MARKET_DATA_KAFKA
def create_topic(bootstrap_servers, topic_name, num_partitions=1, replication_factor=1):
    admin_client = KafkaAdminClient(bootstrap_servers=bootstrap_servers)
    topic = NewTopic(name=topic_name, num_partitions=num_partitions, replication_factor=replication_factor)
    try:
        admin_client.create_topics([topic])
        print(f"Topic '{topic_name}' created successfully.")
    except TopicAlreadyExistsError:
        print(f"Topic '{topic_name}' already exists.")
    except Exception as e:
        print(f"An error occurred while creating topic '{topic_name}': {e}")
    finally:
        admin_client.close()

def initilaise_topics():
    if STREAM_MARKET_DATA_KAFKA:
        for topic in KAFKA_TOPICS:
            create_topic(KAFKA_BOOTSTRAP_SERVERS, topic)
            #in future add config and make appropriate changes to partitions\replication
    else:
        print("Kafka Stream turned off")

def get_kafka_producer():
    return KafkaProducer(bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS)

