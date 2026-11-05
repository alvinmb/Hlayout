# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

import numpy as np, math, sys
from numba import njit
from PIL import Image
T=np.load('T.npy'); C=np.load('C.npy')
@njit(cache=True)
def raster(X,Y,Z,cols,W,H):
    zb=np.full((H,W),1e18); img=np.zeros((H,W,4))
    for i in range(X.shape[0]):
        x0=X[i,0];x1=X[i,1];x2=X[i,2];y0=Y[i,0];y1=Y[i,1];y2=Y[i,2]
        minx=max(int(math.floor(min(x0,min(x1,x2)))),0); maxx=min(int(math.ceil(max(x0,max(x1,x2)))),W-1)
        miny=max(int(math.floor(min(y0,min(y1,y2)))),0); maxy=min(int(math.ceil(max(y0,max(y1,y2)))),H-1)
        den=(y1-y2)*(x0-x2)+(x2-x1)*(y0-y2)
        if abs(den)<1e-12: continue
        for py in range(miny,maxy+1):
            for px in range(minx,maxx+1):
                fx=px+0.5; fy=py+0.5
                a=((y1-y2)*(fx-x2)+(x2-x1)*(fy-y2))/den
                b=((y2-y0)*(fx-x2)+(x0-x2)*(fy-y2))/den
                c=1-a-b
                if a>=-1e-6 and b>=-1e-6 and c>=-1e-6:
                    z=a*Z[i,0]+b*Z[i,1]+c*Z[i,2]
                    if z<zb[py,px]:
                        zb[py,px]=z
                        img[py,px,0]=cols[i,0];img[py,px,1]=cols[i,1];img[py,px,2]=cols[i,2];img[py,px,3]=1.0
    return img
def render(fn, az, el, W=1800, H=1200, box=None, ss=2):
    TT=T; CC=C
    a=math.radians(az); e=math.radians(el)
    Rz=np.array([[math.cos(a),-math.sin(a),0],[math.sin(a),math.cos(a),0],[0,0,1]])
    Rx=np.array([[1,0,0],[0,math.cos(e),-math.sin(e)],[0,math.sin(e),math.cos(e)]])
    R=Rx@Rz; P=TT@R.T
    sx=P[:,:,0]; sy=P[:,:,2]; dep=P[:,:,1]
    n=np.cross(TT[:,1]-TT[:,0],TT[:,2]-TT[:,0]); n/= (np.linalg.norm(n,axis=1)[:,None]+1e-12)
    L=np.array([-0.35,-0.55,0.95]); L/=np.linalg.norm(L)
    sh=0.50+0.50*np.abs(n@L)
    cols=np.clip(CC*sh[:,None],0,1)
    x0,x1,y0,y1=sx.min(),sx.max(),sy.min(),sy.max(); m=0.03*max(x1-x0,y1-y0)
    if box:
        bx0,by0,bx1,by1=box
        corners=np.array([[bx0,by0,0.1],[bx1,by0,0.1],[bx0,by1,0.1],[bx1,by1,0.1]])@R.T
        x0,x1,y0,y1=corners[:,0].min(),corners[:,0].max(),corners[:,2].min(),corners[:,2].max(); m=0
    x0-=m;x1+=m;y0-=m;y1+=m
    s=min(W*ss/(x1-x0),H*ss/(y1-y0)); W2=int((x1-x0)*s); H2=int((y1-y0)*s)
    X=(sx-x0)*s; Y=H2-(sy-y0)*s
    img=raster(X,Y,-dep if False else dep,cols,W2,H2)
    # depth: smaller dep = nearer? camera looks along +y' -> nearer has smaller y'
    im=Image.fromarray((img*255).astype(np.uint8),'RGBA').resize((W2//ss,H2//ss),Image.LANCZOS)
    im.save(fn); print(fn,im.size)
render('alu8_3d_die.png',-28,50,W=1400,H=1000,box=(26,-16,41,-5))
