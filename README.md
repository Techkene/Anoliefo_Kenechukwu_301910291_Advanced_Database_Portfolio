# Advanced Database Systems Portfolio — AquaSense+

**Course:** MIT 8103 Advanced Database Systems · Master of Information Technology · MIVA Open University · 2026/2027 First Semester
**Student:** Anoliefo Kenechukwu · **Matric no.:** 301910291

This repository holds the practical work for all five portfolio activities. Every portfolio uses the same case study and was run on my own laptop. Each folder has its own `README.md` explaining what was built, how it was tested and what the results mean, plus a Jupyter notebook with its outputs saved and an `evidence/` folder.

## Case study: AquaSense+

**AquaSense+** ([www.aquasenseapp.com](https://www.aquasenseapp.com)) is a digital record-keeping platform for Nigerian fish farmers, traders and cooperatives. Farmers record what happens in each pond, and the app shows feed use, survival and profit for every production cycle.

| | |
|---|---|
| **Nature of the business** | Aquaculture (mainly African catfish and tilapia) in earthen ponds, tanks and cages. Each pond runs cycles of stocking → daily feeding and checks → harvest → sale. |
| **Users of the database** | Farmers, farm staff (managers and attendants), traders, cooperative administrators, extension officers |
| **Major data stored** | Farms, ponds/tanks/cages, stocking batches, daily feeding, mortality and growth records, water-quality readings (manual and IoT sensor), feed stock and purchases, harvests, sales, expenses |
| **Key operations** | Stock a pond; record feeding and reduce feed stock; log water quality and deaths; raise low-oxygen alerts; harvest and sell; calculate feed conversion ratio (FCR) and profit; cooperative reports |

The dataset is synthetic but realistic, and it is reproducible because the seed script fixes the random seed. It holds 180 farms in 16 states, 905 ponds, 3,764 production cycles, about 506,000 feeding records and 1.03 million water-quality readings.

## Portfolio map

| # | Folder | Activity | What it shows |
|---|---|---|---|
| 1 | [`Portfolio_1_Database_Design_and_Modelling`](Portfolio_1_Database_Design_and_Modelling) | Database design and modelling | 18-table PostgreSQL schema, ER diagram, keys and constraints, normalisation from UNF to BCNF; 7 out of 7 deliberate rule violations rejected |
| 2 | [`Portfolio_2_Query_optimisation`](Portfolio_2_Query_optimisation) | Query processing and optimisation | 8 real queries with `EXPLAIN (ANALYZE, BUFFERS)` before and after indexing. Composite, partial and covering indexes; sargability; a materialized view; the write cost of indexes. Batch report 2,274 ms → 3 ms |
| 3 | [`Portfolio_3_Transactions_concurrency`](Portfolio_3_Transactions_concurrency) | Transactions and concurrency | Stored procedures; COMMIT, ROLLBACK and SAVEPOINT; two live sessions reproducing a lost update, a non-repeatable read, write skew and a deadlock, each followed by its fix |
| 4 | [`Portfolio_4_nosql`](Portfolio_4_nosql) | NoSQL and advanced data models | Redis key-value implementation: hashes, sorted sets, a stream with consumer groups, geo search, TTL and Lua. Sensor stream → low-oxygen alerts → write-back to PostgreSQL. Also a MongoDB document-model design and a fair benchmark |
| 5 | [`Portfolio_5_Distributed_and_Cloud_Database`](Portfolio_5_Distributed_and_Cloud_Database) | Distributed and cloud databases | A 4-server PostgreSQL lab: streaming replica, async vs sync replication, hash sharding over two servers, shard failure, failover with RTO and RPO measured, plus an AWS deployment design |

## How the portfolios connect

AquaSense+ is a **polyglot persistence** design:
- **PostgreSQL** is the system of record for money, stock, harvests and anything that needs constraints, joins and transactions (Portfolios 1–3).
- **Redis** sits beside it for real-time, short-lived and high-frequency data: latest readings, alerts, login codes and caches (Portfolio 4).
- **Replication, sharding and a cloud deployment** show how the design scales and stays available as farm numbers grow (Portfolio 5).

## Environment

| Component | Version / tool |
|---|---|
| Operating system | Windows |
| Relational database | PostgreSQL 16 (with `postgres_fdw` for sharding) |
| NoSQL | Redis-compatible server (details in the Portfolio 4 README) |
| Notebooks and scripts | Anaconda · Jupyter Notebook · Python 3 with `psycopg2-binary`, `redis`, `pandas`, `matplotlib` |
| Diagrams | Graphviz (ER diagram and cloud architecture) |

## How to reproduce

1. Install PostgreSQL 16 and Anaconda, then run `pip install psycopg2-binary redis pandas matplotlib`.
2. Run the notebooks **in order**, starting with Portfolio 1, which creates the `aquasense` database. Each notebook asks for the PostgreSQL password, so no password is stored in this repository.
3. Portfolio 4 also needs a Redis-compatible server on port 6379.
4. Portfolio 5 builds its four lab servers in `~/aquasense_cluster`, outside this repository, and leaves the main PostgreSQL untouched.

Timings depend on the computer. Plan shapes, row counts and behaviours should match.

## References

- PostgreSQL Global Development Group (2026) *PostgreSQL 16 Documentation*. https://www.postgresql.org/docs/16/
- Redis Ltd. (2026) *Redis documentation*. https://redis.io/docs/
- MongoDB Inc. (2026) *MongoDB Manual*. https://www.mongodb.com/docs/manual/
- Amazon Web Services (2023) *AWS Local Zones in Lagos generally available*. https://aws.amazon.com/about-aws/whats-new/2023/01/aws-local-zones-lagos-lima-queretaro/
- Federal Republic of Nigeria (2023) *Nigeria Data Protection Act 2023*.
- Elmasri, R. and Navathe, S.B. (2016) *Fundamentals of Database Systems*. 7th edn. Pearson.
- Kleppmann, M. (2017) *Designing Data-Intensive Applications*. O'Reilly.

## AI use declaration

AI assistance (Claude, by Anthropic) was used to generate first versions of the SQL, Python scripts, notebooks and documentation. I ran every portfolio on my own machine, and the evidence, screenshots and README figures come from my own runs. Problems found while running and checking the work were corrected and are recorded in each portfolio's README. The full declaration is in the LMS submission.
