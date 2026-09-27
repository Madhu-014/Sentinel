import os
import json
import uuid
from datetime import datetime
from kafka import KafkaConsumer
from cassandra.cluster import Cluster

def main():
    cluster = Cluster([os.environ.get('CASSANDRA_HOST', '127.0.0.1')], port=int(os.environ.get('CASSANDRA_PORT', 9042)))
    session = cluster.connect('sentinel')

    consumer = KafkaConsumer(
        'sentinel.requests',
        bootstrap_servers=[os.environ.get('KAFKA_BOOTSTRAP_SERVERS', '127.0.0.1:9092')],
        auto_offset_reset='earliest',
        enable_auto_commit=True,
        group_id='cassandra_writer2', # Change group to re-read if needed
        value_deserializer=lambda m: json.loads(m.decode('utf-8'))
    )

    query = session.prepare("""
        INSERT INTO request_logs 
        (request_id, request_date, request_timestamp, request_text, phase1_score, 
         phase1_tier, classifier_verdict, classifier_confidence, 
         judge_category, judge_confidence, final_decision, disagreement_flag)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """)

    print("Listening to Kafka and writing to Cassandra...")
    for message in consumer:
        try:
            record = message.value
            ts_str = record['timestamp'].replace("Z", "+00:00")
            ts = datetime.fromisoformat(ts_str)

            session.execute(query, (
                uuid.UUID(record['request_id']),
                record['request_date'],
                ts,
                record['request_text'],
                record.get('phase1_score'),
                record.get('phase1_tier'),
                record.get('classifier_verdict'),
                record.get('classifier_confidence'),
                record.get('judge_category'),
                record.get('judge_confidence'),
                record.get('final_decision'),
                record.get('disagreement_flag', False)
            ))
            print(f"Inserted: {record['request_id']} | {record['final_decision']} | df={record.get('disagreement_flag', False)}")
        except Exception as e:
            print(f"Error processing record: {e}")
            continue

if __name__ == "__main__":
    main()
