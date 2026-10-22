// Deterministic TEST-only evidence rules. No runtime API or browser CDP.
export function matchHistory(rows, task, targetName) {
  const prefix='/home/wdguest/Downloads/';
  const matches=rows.filter(r=>typeof r.guid==='string'&&r.guid.length>0&&
    Array.isArray(r.urls)&&r.urls.includes(task.url)&&!task.beforeIds.includes(r.id)&&
    typeof r.target_path==='string'&&r.target_path.startsWith(prefix)&&
    !r.target_path.slice(prefix.length).includes('/')&&
    (!targetName||r.target_path===prefix+targetName));
  return matches.length===1?matches[0]:null;
}
export function rejectedUI(key, ui) {
  if(!ui.name_visible||ui.other_names_visible.length||!ui.failed)return false;
  if(key==='reset')return ui.network_failure||ui.file_incomplete;
  if(key==='symlink')return ui.permission_failure;
  if(key==='space'||key==='inodes')return ui.disk_full;
  return false;
}
