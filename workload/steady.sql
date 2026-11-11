\set cust_id random(1, 186740)
\set rent_id random(1, 1423080)
SELECT customer_id, full_name, email FROM bluebox.customer WHERE customer_id = :cust_id;
SELECT rental_id, rental_period, inventory_id FROM bluebox.rental WHERE customer_id = :cust_id LIMIT 5;
SELECT film_id, title, popularity FROM bluebox.film ORDER BY popularity DESC LIMIT 5;
UPDATE bluebox.customer SET last_update = now() WHERE customer_id = :cust_id;
UPDATE bluebox.rental SET last_update = now() WHERE rental_id = :rent_id;
