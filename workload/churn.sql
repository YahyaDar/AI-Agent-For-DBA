\set id random(1, 20000)
\set delta random(1, 100)
UPDATE workload.churn_items SET counter = counter + :delta, payload = repeat('x', 200), updated_at = now() WHERE id = :id;
DELETE FROM workload.churn_items WHERE id = :id;
INSERT INTO workload.churn_items (id, payload, counter, updated_at) VALUES (:id, repeat('y', 200), :delta, now()) ON CONFLICT (id) DO UPDATE SET counter = excluded.counter + 1;
