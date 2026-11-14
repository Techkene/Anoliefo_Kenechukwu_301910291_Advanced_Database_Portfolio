# AI Use Declaration


**1. AI tool used**
Claude (Anthropic), Opus 5.5 model, via claude.ai.

**2. What the AI tool was used for**
- Generating a first version of the PostgreSQL schema, synthetic seed-data script, SQL queries,
  stored procedures and Python/Bash test scripts for all five portfolios, based on my chosen case
  study (AquaSense+).
- Running the scripts in a sandbox and drafting the README explanations and this documentation from
  the actual outputs.
- Drafting the ER diagram and cloud architecture diagram (Graphviz source).

**3. How the output was reviewed, corrected, tested and verified**
- I re-ran every script myself on my own PostgreSQL 16 / Redis 7 installation and compared my
  outputs with the evidence files; screenshots in the LMS submission are from my own runs.
  I found issues like in compactibility with my window but the programs that needed administrator priviledge
- Problems found and fixed during development (visible in the commit history and READMEs):
  the seed data originally placed all farms in 4 states and stocked every pond on the same days
  (fixed so data is spread over 16 states and staggered dates); custom error codes P0002/P0003
  clashed with PL/pgSQL's reserved codes (changed to AQ001–AQ003); an initial claim that Redis was
  faster than PostgreSQL was contradicted by the measurements and was rewritten using a fair
  `redis-benchmark` vs `pgbench` comparison.
- I checked each design decision against the course material (normalisation, isolation levels,
  indexing, CAP/PACELC) and can explain each script line by line.
  Since I understand I have done.

I understand that using AI does not remove my responsibility for the accuracy, originality and
understanding of the submitted work.
