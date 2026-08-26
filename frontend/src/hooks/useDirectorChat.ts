/** 作者↔导演 共创对话（逐 token 流式）。
 *
 * 与模拟 SSE（EventSource 只支持 GET）不同，对话走 POST → fetch Reader 解析
 * text/event-stream 帧：director_chat_delta 逐 token 追加到最新一条导演气泡，
 * director_chat_done 收尾，director_chat_error 展示兜底文案。
 */
import { useEffect, useRef, useState } from 'react';
import { API_BASE } from '../types/types';

export interface ChatMsg {
  id: string;
  role: 'author' | 'director';
  text: string;
  streaming?: boolean;
  error?: string;
}

function parseFrame(frame: string): { event: string; data: Record<string, string> } | null {
  let event = 'message';
  const lines: string[] = [];
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) lines.push(line.slice(5).trim());
  }
  if (!lines.length) return null;
  try {
    return { event, data: JSON.parse(lines.join('\n')) as Record<string, string> };
  } catch {
    return { event, data: { text: lines.join('\n') } };
  }
}

export function useDirectorChat(simId: string | null) {
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const append = (m: ChatMsg) => setMessages((prev) => [...prev, m]);
  const patchLast = (patch: Partial<ChatMsg>) =>
    setMessages((prev) => {
      if (!prev.length) return prev;
      const arr = [...prev];
      arr[arr.length - 1] = { ...arr[arr.length - 1], ...patch };
      return arr;
    });

  const send = async (raw: string) => {
    const sid = simId;
    const text = raw.trim();
    if (!sid || !text || busy) return;

    append({ id: `a-${Date.now()}`, role: 'author', text });
    append({ id: `d-${Date.now()}`, role: 'director', text: '', streaming: true });
    setBusy(true);

    const ac = new AbortController();
    abortRef.current = ac;
    let full = '';
    try {
      const res = await fetch(`${API_BASE}/api/v1/sims/${sid}/director/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text }),
        signal: ac.signal,
      });
      if (!res.ok) {
        // 非 200（如 LLM 未接入的 503）→ 兜底文案进气泡
        let detail = `导演无法回应（${res.status}）`;
        try {
          const j = (await res.json()) as { detail?: string };
          if (j.detail) detail = j.detail;
        } catch { /* 非 JSON 响应 */ }
        patchLast({ streaming: false, error: detail });
        setBusy(false);
        return;
      }
      const reader = res.body?.getReader();
      if (!reader) throw new Error('响应无 body');
      const decoder = new TextDecoder();
      let buf = '';
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const frames = buf.split('\n\n');
        buf = frames.pop() ?? '';
        for (const frame of frames) {
          const evt = parseFrame(frame);
          if (!evt) continue;
          if (evt.event === 'director_chat_delta' && evt.data.delta != null) {
            full += evt.data.delta;
            patchLast({ text: full });
          } else if (evt.event === 'director_chat_error') {
            patchLast({ streaming: false, error: evt.data.message || '导演暂时无法回应' });
          } else if (evt.event === 'director_chat_done') {
            full = evt.data.text ?? full;
            patchLast({ streaming: false, text: full });
          }
        }
      }
      // 流正常收尾但无 done 事件（兜底）：确保气泡不再转圈
      patchLast({ streaming: false, text: full || '（本次对话导演没有留下文字）' });
    } catch (e) {
      if ((e as Error).name !== 'AbortError') {
        patchLast({ streaming: false, error: '连接导演失败，请确认后端是否在线' });
      }
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  };

  const stop = () => abortRef.current?.abort();

  // simId 切换（重开 sim）时清空旧对话
  useEffect(() => {
    setMessages([]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [simId]);

  return { messages, busy, send, stop };
}