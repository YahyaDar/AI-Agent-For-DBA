\set rent_id random(1, 1423080)
UPDATE workload.churn_big SET last_update = now() WHERE rental_id = :rent_id;
