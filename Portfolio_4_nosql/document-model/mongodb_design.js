// =====================================================================
// AquaSense+ | Portfolio 4 | MongoDB document model — DESIGN COMPARISON
// Status: design script (mongosh syntax). MongoDB could not be installed in
// the environment used for this portfolio, so this file was NOT executed;
// the implemented NoSQL store is Redis (redis_aquasense.py). It can be run
// on a free MongoDB Atlas cluster:  mongosh "<connection-string>" mongodb_design.js
// The example document (example_batch_document.json) was generated from the
// real PostgreSQL data with jsonb_build_object.
// =====================================================================
const db = db.getSiblingDB("aquasense");

// 1) batches: one document per production cycle, daily records EMBEDDED.
//    Bounded: a cycle is ~150–300 days -> ~170 small entries (~15 KB), far below
//    the 16 MB document limit. The app's "batch screen" becomes ONE read.
db.createCollection("batches", {
  validator: { $jsonSchema: {
    bsonType: "object",
    required: ["farm", "unit", "species", "stocked_on", "status", "stocking"],
    properties: {
      status:   { enum: ["active", "harvested", "closed"] },
      stocking: { bsonType: "object", required: ["count"],
                  properties: { count: { bsonType: "int", minimum: 1 } } },
      daily:    { bsonType: "array", maxItems: 400,
                  items: { bsonType: "object", required: ["date", "feed_kg"],
                           properties: { feed_kg: { bsonType: ["double", "decimal"], minimum: 0 } } } }
    } } }
});
db.batches.createIndex({ "farm.farm_id": 1, status: 1, stocked_on: -1 });
db.batches.createIndex({ "farm.location": "2dsphere" });                     // trader "farms near me"
db.batches.createIndex({ "unit.unit_id": 1 }, { unique: true,
                         partialFilterExpression: { status: "active" } }); // one active batch per pond

// Offline sync: append today's record to the batch in one atomic single-document update
db.batches.updateOne(
  { _id: "batch_316", status: "active" },
  { $push: { daily: { date: ISODate("2026-11-04"), feed_kg: 12.5, feed: "Coppens 4.0mm", dead: 3 } },
    $inc:  { "summary.feed_kg": 12.5, "summary.dead": 3 } });

// 2) readings: sensor data is UNBOUNDED, so it is NOT embedded in the batch.
//    Bucket pattern — one document per pond per day holding up to 12–96 readings.
//    (MongoDB 5+ alternative: a native time-series collection.)
db.createCollection("readings", { timeseries: { timeField: "ts", metaField: "pond", granularity: "hours" },
                                  expireAfterSeconds: 60 * 60 * 24 * 400 });
db.readings.insertOne({ pond: { unit_id: 316, farm_id: 64 }, ts: ISODate("2026-11-03T22:00:00Z"),
                        do: 4.42, ph: 6.78, temp: 27.3, nh3: 0.322 });

// 3) Aggregation: feed conversion ratio per farm for 2025 harvests
db.batches.aggregate([
  { $match: { status: "harvested", "harvest.date": { $gte: ISODate("2025-01-01") } } },
  { $project: { farm: "$farm.name",
                gain_kg: { $subtract: ["$harvest.total_kg",
                           { $divide: [{ $multiply: ["$stocking.count", "$stocking.avg_weight_g"] }, 1000] }] },
                feed_kg: { $sum: "$daily.feed_kg" } } },
  { $group: { _id: "$farm", feed: { $sum: "$feed_kg" }, gain: { $sum: "$gain_kg" } } },
  { $project: { fcr: { $round: [{ $divide: ["$feed", "$gain"] }, 2] } } },
  { $sort: { fcr: 1 } }, { $limit: 5 }
]);
