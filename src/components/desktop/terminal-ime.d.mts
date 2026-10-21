import type {Terminal} from '@xterm/xterm';
export function attachTerminalIme(term: Terminal): {setEnabled(value: boolean): void; dispose(): void};
