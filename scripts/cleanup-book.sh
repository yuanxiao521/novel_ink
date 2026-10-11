#!/usr/bin/env bash
# 删除一本书及其全部从属行（外键无 CASCADE，按依赖顺序；与清理两本坏书同一套顺序）。
# 用法：bash scripts/cleanup-book.sh <book_id>
set -euo pipefail
BID="${1:?用法: cleanup-book.sh <book_id>}"
docker exec -i novel-ink-db psql -U postgres -d novel -v ON_ERROR_STOP=1 -v bid="$BID" <<'SQL'
begin;
delete from agent_messages where task_id in (select id from agent_tasks where book_id = :'bid');
delete from agent_tasks where book_id = :'bid';
delete from events where sim_id in (select id from simulation where book_id = :'bid');
delete from simulation where book_id = :'bid';
delete from prose_annotations where scene_id in (select s.id from scenes s join chapters c on c.id = s.chapter_id where c.book_id = :'bid');
delete from prose_notes where scene_id in (select s.id from scenes s join chapters c on c.id = s.chapter_id where c.book_id = :'bid');
delete from characters where book_id = :'bid' or scene_id in (select s.id from scenes s join chapters c on c.id = s.chapter_id where c.book_id = :'bid');
delete from scenes where chapter_id in (select id from chapters where book_id = :'bid');
delete from chapters where book_id = :'bid';
delete from foreshadows where book_id = :'bid';
delete from inspiration_cards where book_id = :'bid';
delete from book_memories where book_id = :'bid';
delete from chat_histories where book_id = :'bid';
delete from world_states where book_id = :'bid';
delete from beliefs where book_id = :'bid';
delete from books where id = :'bid';
commit;
SQL
echo "cleaned: $BID"
