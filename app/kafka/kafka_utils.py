import logging
from kafka import KafkaProducer
from kafka.admin import KafkaAdminClient, NewTopic
from kafka.errors import TopicAlreadyExistsError
from app.config.config import KAFKA_TOPICS, KAFKA_BOOTSTRAP_SERVERS, STREAM_MARKET_DATA_KAFKA

logger = logging.getLogger(__name__)


def create_topic(bootstrap_servers, topic_name, num_partitions=1, replication_factor=1):
    """
    Create a Kafka topic if it doesn't exist.

    Args:
        bootstrap_servers: Kafka bootstrap server addresses
        topic_name: Name of the topic to create
        num_partitions: Number of partitions for the topic
        replication_factor: Replication factor for the topic
    """
    admin_client = KafkaAdminClient(bootstrap_servers=bootstrap_servers)
    topic = NewTopic(name=topic_name, num_partitions=num_partitions, replication_factor=replication_factor)
    try:
        admin_client.create_topics([topic])
        logger.info(f"Kafka topic '{topic_name}' created successfully")
    except TopicAlreadyExistsError:
        logger.debug(f"Kafka topic '{topic_name}' already exists")
    except Exception as e:
        logger.error(f"Error creating Kafka topic '{topic_name}': {e}")
    finally:
        admin_client.close()


def create_ticker_topics_for_symbols(symbols: list):
    """
    Create Kafka topics for ticker streams (one topic per symbol).

    Topic format: binance.ticks.{SYMBOL}
    Example: binance.ticks.BTCUSDT

    Args:
        symbols: List of trading pair symbols
    """
    if not STREAM_MARKET_DATA_KAFKA:
        logger.info("Kafka streaming disabled, skipping ticker topic creation")
        return

    logger.info(f"Creating ticker topics for {len(symbols)} symbols")
    for symbol in symbols:
        topic_name = f"binance.ticks.{symbol.upper()}"
        create_topic(KAFKA_BOOTSTRAP_SERVERS, topic_name, num_partitions=1, replication_factor=1)

    logger.info("Ticker topic creation completed")


def initilaise_topics():
    """
    Initialize standard Kafka topics on startup.

    Note: Ticker topics are created dynamically based on active symbols.
    """
    if STREAM_MARKET_DATA_KAFKA:
        logger.info("Initializing standard Kafka topics")
        for topic in KAFKA_TOPICS:
            create_topic(KAFKA_BOOTSTRAP_SERVERS, topic)
            #in future add config and make appropriate changes to partitions\replication
        logger.info("Standard Kafka topics initialized")
    else:
        logger.info("Kafka streaming disabled")


def get_kafka_producer():
    """
    Get a Kafka producer instance.

    Returns:
        KafkaProducer instance configured with bootstrap servers
    """
    return KafkaProducer(bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS)

