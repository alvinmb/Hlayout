# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

import gdstk, json, matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
from shapely.geometry import LineString
import sys
G='/sessions/'+__import__('os').environ['HOME'].split('/sessions/')[1]+'/mnt/HLayout/example5/TOP.gds' if False else __import__('os').path.expanduser('~/mnt/HLayout/example5/TOP.gds')
lib=gdstk.read_gds(G); cells={c.name:c for c in lib.cells}
cols={l['number']:l['color'] for l in json.load(open(G+'.hlaux'))['technology']['layers']}
def polys(cell):
    f=cell.copy('F_'+cell.name); f.flatten(); out={}
    for p in f.polygons: out.setdefault(p.layer,[]).append(p.points)
    for p in f.paths:
        for q in p.to_polygons(): out.setdefault(q.layer,[]).append(q.points)
    return out, f
order=[57,3,4,5,20,58,49,61,63,59,60]
alpha={57:1,3:.85,4:.35,5:.85,20:.5,58:1,49:.9,61:0,63:0,59:1,60:1}
def render(cell, fn, bbox=None, dpi=200, w=11):
    P,f=polys(cell)
    (x0,y0),(x1,y1)=bbox or f.bounding_box()
    h=w*(y1-y0)/(x1-x0)
    fig=plt.figure(figsize=(w,h)); ax=fig.add_axes([0,0,1,1]); ax.set_facecolor('#111418'); fig.patch.set_facecolor('#111418')
    for L in order:
        if L not in P: continue
        c=cols.get(L,'#888888')
        if L==57: c='#d9d4c7'
        fc=c if alpha[L]>0 else 'none'
        pc=PolyCollection(P[L], facecolors=fc, edgecolors=c if alpha[L]==0 or L in (59,60) else 'none', linewidths=.4, alpha=None if alpha[L]==0 else alpha[L])
        ax.add_collection(pc)
    for lb in f.labels:
        if lb.layer==62: ax.text(lb.origin[0],lb.origin[1],lb.text,color='white',fontsize=9,ha='center',va='center',weight='bold')
    ax.set_xlim(x0,x1); ax.set_ylim(y0,y1); ax.set_aspect('equal'); ax.axis('off')
    fig.savefig(fn,dpi=dpi,facecolor=fig.get_facecolor()); plt.close(fig)
render(cells['TOP'],'alu8_layout.png',dpi=130)
# die detail around U1
f=cells['TOP']; r=[x for x in f.references if x.cell.name=='DIE_54AS181_U1'][0]; cx,cy=r.origin
render(cells['TOP'],'alu8_die.png',bbox=((cx-6500,cy-4800),(cx+6500,cy+4800)),dpi=110,w=9)
# assembly drawing
A=cells['ASSEMBLY']; F=A.copy('FA'); F.flatten()
(x0,y0),(x1,y1)=F.bounding_box(); w=8; h=w*(y1-y0)/(x1-x0)
fig=plt.figure(figsize=(w,h)); ax=fig.add_axes([0,0,1,1]); fig.patch.set_facecolor('white')
segs=[]
for p in F.paths:
    for q in p.to_polygons(): segs.append(q.points)
ax.add_collection(PolyCollection(segs,facecolors='#16202e',edgecolors='none'))
for p in F.polygons: ax.add_collection(PolyCollection([p.points],facecolors='#16202e'))
for lb in F.labels: ax.text(lb.origin[0],lb.origin[1],lb.text,fontsize=4,color='#16202e')
ax.set_xlim(x0,x1); ax.set_ylim(y0,y1); ax.set_aspect('equal'); ax.axis('off')
fig.savefig('alu8_assembly.png',dpi=150); plt.close(fig)
print('done', len(F.labels), len(F.paths), len(F.polygons))
