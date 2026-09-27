CREATE KEYSPACE IF NOT EXISTS sentinel 
  WITH REPLICATION = { 
   'class' : 'SimpleStrategy', 
   'replication_factor' : 1 
  };

USE sentinel;

CREATE TABLE IF NOT EXISTS request_logs (
    request_date date,
    request_timestamp timestamp,
    request_id uuid,
    request_text text,
    phase1_score float,
    phase1_tier text,
    classifier_verdict text,
    classifier_confidence float,
    judge_category text,
    judge_confidence float,
    final_decision text,
    PRIMARY KEY ((request_date), request_timestamp, request_id)
) WITH CLUSTERING ORDER BY (request_timestamp DESC, request_id ASC);
