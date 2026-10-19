import os from "os";
import path from "path";
import fsp from "fs/promises";

export const HOME = process.env.HOME || os.homedir();
export const DESKTOP = path.join(HOME, "Desktop");
export const DOCUMENTS = path.join(HOME, "Documents");
export const DOWNLOADS = path.join(HOME, "Downloads");
export const PICTURES = path.join(HOME, "Pictures");
export const TRASH_ROOT = path.join(HOME, ".local/share/Trash");
export const TRASH_FILES = path.join(TRASH_ROOT, "files");
export const TRASH_INFO = path.join(TRASH_ROOT, "info");

export function resolveP(p?: string | null) {
  if (!p || p === "~") return HOME;
  if (p.startsWith("~/")) return path.join(HOME, p.slice(2));
  return path.resolve(p);
}

const VNC_FM_DESKTOP = `[Desktop Entry]
Type=Application
Name=VNC 文件管理器
Name[zh_CN]=VNC 文件管理器
Comment=在 VNC 窗口中运行 PCManFM，可测试直接粘贴桌面复制的文件或图片
Exec=pcmanfm ${DOCUMENTS}
Icon=system-file-manager
Terminal=false
`;

async function ensureVncFileManagerShortcut() {
  const fmLauncher = path.join(DESKTOP, "VNC文件管理器.desktop");
  try {
    await fsp.access(fmLauncher);
  } catch {
    await fsp.writeFile(fmLauncher, VNC_FM_DESKTOP, { mode: 0o755 }).catch(() => {});
    await fsp.chmod(fmLauncher, 0o755).catch(() => {});
  }
}

let ensured: Promise<void> | null = null;
export function ensureHome() {
  ensured ??= (async () => {
    try {
      for (const d of [DESKTOP, DOCUMENTS, DOWNLOADS, PICTURES, TRASH_FILES, TRASH_INFO]) {
        await fsp.mkdir(d, { recursive: true });
      }
      await ensureVncFileManagerShortcut();
    } catch (e) {
      console.error("ensureHome failed", e);
    }
  })();
  return ensured;
}

export type SysEntry = {
  name: string;
  path: string;
  kind: "folder" | "file";
  link: boolean;
  size: number;
  mtime: number;
  mode: string;
  hidden: boolean;
};

export async function listDir(dir: string, hidden: boolean): Promise<SysEntry[]> {
  const names = (await fsp.readdir(dir)).slice(0, 4000);
  const rows = await Promise.all(
    names
      .filter((n) => hidden || !n.startsWith("."))
      .map(async (name): Promise<SysEntry | null> => {
        const full = path.join(dir, name);
        try {
          const ls = await fsp.lstat(full);
          let st = ls;
          const link = ls.isSymbolicLink();
          if (link) {
            try {
              st = await fsp.stat(full);
            } catch {
              /* 断开的链接 */
            }
          }
          return {
            name,
            path: full,
            kind: st.isDirectory() ? "folder" : "file",
            link,
            size: st.size,
            mtime: st.mtimeMs,
            mode: (st.mode & 0o777).toString(8).padStart(3, "0"),
            hidden: name.startsWith("."),
          };
        } catch {
          return null;
        }
      }),
  );
  return rows
    .filter((r): r is SysEntry => r !== null)
    .sort((a, b) =>
      a.kind !== b.kind ? (a.kind === "folder" ? -1 : 1) : a.name.localeCompare(b.name, "zh"),
    );
}

// 禁止对关键路径做删除 / 重命名 / 移动
export function isProtected(p: string) {
  const parts = p.split(path.sep).filter(Boolean);
  if (parts.length <= 1) return true;
  const fixed = [
    HOME,
    DESKTOP,
    DOCUMENTS,
    DOWNLOADS,
    PICTURES,
    TRASH_ROOT,
    TRASH_FILES,
    TRASH_INFO,
    path.join(HOME, ".local"),
    path.join(HOME, ".local/share"),
  ];
  if (fixed.includes(p)) return true;
  const cwd = process.cwd();
  if (cwd === p || cwd.startsWith(p + path.sep)) return true; // 运行本网站的目录
  if (HOME.startsWith(p + path.sep)) return true;
  return false;
}

export function isInTrash(p: string) {
  return p.startsWith(TRASH_FILES + path.sep);
}

export async function exists(p: string) {
  try {
    await fsp.lstat(p);
    return true;
  } catch {
    return false;
  }
}

export async function movePath(src: string, dst: string) {
  try {
    await fsp.rename(src, dst);
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "EXDEV") {
      await fsp.cp(src, dst, { recursive: true, preserveTimestamps: true });
      await fsp.rm(src, { recursive: true, force: true });
    } else throw e;
  }
}

/** 生成目标目录中不重名的文件路径（例如 "示例图片 - 副本.png" 或 "示例图片 (2).png"） */
export async function uniqueDestPath(destDir: string, baseNameStr: string, sameDirCopy = false): Promise<string> {
  const ext = path.extname(baseNameStr);
  const stem = ext ? baseNameStr.slice(0, -ext.length) : baseNameStr;
  const firstCandidate = sameDirCopy ? path.join(destDir, `${stem} - 副本${ext}`) : path.join(destDir, baseNameStr);
  if (!(await exists(firstCandidate))) return firstCandidate;
  for (let i = 2; i < 1000; i++) {
    const candidate = sameDirCopy
      ? path.join(destDir, `${stem} - 副本 (${i})${ext}`)
      : path.join(destDir, `${stem} (${i})${ext}`);
    if (!(await exists(candidate))) return candidate;
  }
  return path.join(destDir, `${stem}-${Date.now()}${ext}`);
}

/** 复制或剪切（移动）文件/文件夹到目标目录 */
export async function copyEntryToDir(src: string, destDir: string, cut = false): Promise<string> {
  const st = await fsp.lstat(src);
  const dirStat = await fsp.stat(destDir);
  if (!dirStat.isDirectory()) throw new Error("目标不是目录");

  const srcDir = path.dirname(src);
  const base = path.basename(src);
  if (cut && srcDir === destDir) return src;
  if (st.isDirectory() && (destDir === src || destDir.startsWith(src + path.sep))) {
    throw new Error("不能把文件夹复制或移动到它自己里面");
  }

  const dst = await uniqueDestPath(destDir, base, !cut && srcDir === destDir);
  if (cut) {
    if (isProtected(src)) throw new Error("受保护的系统路径，不能移动");
    await movePath(src, dst);
  } else {
    await fsp.cp(src, dst, { recursive: true, preserveTimestamps: true });
  }
  return dst;
}

export async function moveToTrash(p: string) {
  await ensureHome();
  const base = path.basename(p);
  let name = base;
  if (await exists(path.join(TRASH_FILES, name))) name = `${base}.${Date.now()}`;
  await movePath(p, path.join(TRASH_FILES, name));
  await fsp.writeFile(
    path.join(TRASH_INFO, name + ".json"),
    JSON.stringify({ original: p, deletedAt: Date.now() }),
  );
}

export async function restoreFromTrash(p: string) {
  const name = path.basename(p);
  let original = path.join(DOCUMENTS, name);
  try {
    const info = JSON.parse(await fsp.readFile(path.join(TRASH_INFO, name + ".json"), "utf8"));
    if (typeof info.original === "string") original = info.original;
  } catch {
    /* 没有记录则还原到 Documents */
  }
  await fsp.mkdir(path.dirname(original), { recursive: true });
  let dest = original;
  if (await exists(dest)) dest = `${original} (已还原)`;
  await movePath(p, dest);
  await fsp.rm(path.join(TRASH_INFO, name + ".json"), { force: true });
  return dest;
}

export async function destroyTrashed(p: string) {
  await fsp.rm(p, { recursive: true, force: true });
  await fsp.rm(path.join(TRASH_INFO, path.basename(p) + ".json"), { force: true });
}

export async function emptyTrash() {
  await ensureHome();
  for (const n of await fsp.readdir(TRASH_FILES)) {
    await fsp.rm(path.join(TRASH_FILES, n), { recursive: true, force: true });
  }
  for (const n of await fsp.readdir(TRASH_INFO)) {
    await fsp.rm(path.join(TRASH_INFO, n), { recursive: true, force: true });
  }
}

export function fsError(e: unknown) {
  const code = (e as NodeJS.ErrnoException)?.code;
  const map: Record<string, [number, string]> = {
    ENOENT: [404, "路径不存在"],
    EACCES: [403, "权限不足"],
    EPERM: [403, "操作不被允许"],
    EEXIST: [409, "同名文件已存在"],
    ENOTDIR: [400, "不是目录"],
    EISDIR: [400, "这是一个目录"],
    ENOTEMPTY: [409, "目录非空"],
    ENOSPC: [507, "磁盘空间不足"],
    EROFS: [403, "只读文件系统"],
  };
  const m = code ? map[code] : undefined;
  if (m) return Response.json({ error: m[1] }, { status: m[0] });
  return Response.json({ error: e instanceof Error ? e.message : "操作失败" }, { status: 500 });
}

export function validName(name: unknown): name is string {
  return (
    typeof name === "string" &&
    name.length > 0 &&
    name.length <= 255 &&
    !name.includes("/") &&
    !name.includes("\0") &&
    name !== "." &&
    name !== ".."
  );
}
