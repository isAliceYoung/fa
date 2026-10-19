// UTF-16 slicing must preserve astral code points. Four thousand code units
// also fit the IPC UTF-8 and JSON byte budgets for CJK and control characters.
export function* terminalInputFrames(data) {
  for (let start = 0; start < data.length;) {
    let end = Math.min(start + 4096, data.length);
    const last = data.charCodeAt(end - 1), next = data.charCodeAt(end);
    if (end < data.length && last >= 0xd800 && last <= 0xdbff && next >= 0xdc00 && next <= 0xdfff) end--;
    yield { t: 'i', d: data.slice(start, end) };
    start = end;
  }
}
