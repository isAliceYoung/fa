export function terminalAction(data: string): { app: 'gui' | 'sysfiles' | 'sysedit'; path: string; label: string; cmd?: string } | null;
