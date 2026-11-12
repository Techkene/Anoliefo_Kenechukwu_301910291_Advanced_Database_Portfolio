SELECT recorded_at, ph, dissolved_oxygen_mgl, temperature_c, ammonia_mgl FROM aqua.water_quality_reading WHERE unit_id = 316 ORDER BY recorded_at DESC LIMIT 1;
