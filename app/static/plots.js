import {element} from './client.js';

function block(target,className,content) {
  const node=element('div',content,{class:className});
  target.append(node);
  return node;
}

export function visualize(report,selected) {
  const target=document.querySelector('#visual');
  target.replaceChildren();
  const measurement=report.cases?.find(row=>row.case===selected)?.measure;
  if (!measurement) {block(target,'chart-note','选择有计算值的分组');return;}
  const e=measurement.evidence;

  for (const [title,entries] of [['快照可见',e.visible.map(id=>[id,'保留'])],['删除记录',Object.entries(e.removed)],['悬空删除',e.dangling.map(id=>[id,'检查偏移'])]]) {
    const shelf=block(target,'lake-shelf',title);
    for(const [key,kind] of entries) block(shelf,'lake-key',`${key} · ${kind}`);
  }
}
