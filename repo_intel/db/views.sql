-- db/views.sql
CREATE OR REPLACE VIEW v_file_hotspots AS
SELECT f.repo_id, f.file_id, f.file_path, f.language, f.line_count,
       COUNT(*)                                         AS changes,
       SUM(cf.additions + cf.deletions)                 AS churn,
       COUNT(DISTINCT c.author_email)                   AS authors,
       SUM(c.committed_at >= NOW() - INTERVAL 90 DAY)   AS changes_90d,
       COUNT(*) * LN(1 + f.line_count)                  AS hotspot_score
FROM commit_files cf
JOIN commits c ON c.commit_id = cf.commit_id
JOIN files   f ON f.file_id   = cf.file_id
WHERE f.exists_at_head = 1
GROUP BY f.file_id;

CREATE OR REPLACE VIEW v_file_ownership AS      -- bus factor input: who owns each file
SELECT repo_id, file_id, file_path, top_author, total_changes,
       author_changes / total_changes AS top_share
FROM (
  SELECT f.repo_id, f.file_id, f.file_path, c.author_email AS top_author,
         COUNT(*) AS author_changes,
         SUM(COUNT(*)) OVER (PARTITION BY f.file_id) AS total_changes,
         ROW_NUMBER() OVER (PARTITION BY f.file_id ORDER BY COUNT(*) DESC) AS rn
  FROM commit_files cf
  JOIN commits c ON c.commit_id = cf.commit_id
  JOIN files   f ON f.file_id   = cf.file_id
  WHERE f.exists_at_head = 1
  GROUP BY f.repo_id, f.file_id, f.file_path, c.author_email
) t WHERE rn = 1;

CREATE OR REPLACE VIEW v_contributor_stats AS
SELECT repo_id, author_email, MAX(author_name) AS author_name, COUNT(*) AS commits,
       SUM(additions) AS additions, SUM(deletions) AS deletions,
       MIN(committed_at) AS first_commit, MAX(committed_at) AS last_commit
FROM commits GROUP BY repo_id, author_email;