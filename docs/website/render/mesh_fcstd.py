# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

import zipfile,os,numpy as np, sys
from OCP.BRepTools import BRepTools
from OCP.TopoDS import TopoDS_Shape, TopoDS
from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
from OCP.TopLoc import TopLoc_Location
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
z=zipfile.ZipFile(os.path.expanduser('~/mnt/HLayout/example5/freecad/freecad_TOP.FCStd'))
def col(n):
    n=n.lower()
    if n.startswith('substrate'): return (0.93,0.92,0.88)
    if 'conductor_l3' in n: return (0.85,0.66,0.22)
    if 'conductor_l5' in n: return (0.92,0.74,0.28)
    if 'dielectric' in n: return (0.22,0.55,0.45)
    if n.startswith('u') and 'die' in n: return (0.28,0.30,0.36)
    if 'wire_bonds' in n: return (0.98,0.82,0.30)
    if n.startswith('part__feature.') or n=='part__feature': return (0.70,0.72,0.74)  # body
    return None
tris=[];cols=[]
names=[n for n in z.namelist() if n.endswith('.Shape.brp')]
bb=Bnd_Box()
for n in names:
    obj=n[:-10]
    import re
    c=col(obj)
    if c is None:
        # seals / pins via label lookup
        k=int(re.sub(r'\D','',obj) or 0) if obj.startswith('Part__Feature') else -1
        if obj.startswith('Part__Feature'):
            c=(0.30,0.55,0.35) if 1<=k<=40 else (0.95,0.78,0.30)
        else: continue
    s=TopoDS_Shape(); BRepTools.Read_s(s, __import__('io').BytesIO(z.read(n)) if False else write_tmp(z,n) if False else (open('t.brp','wb').write(z.read(n)) and 't.brp'), BRep_Builder())
    BRepMesh_IncrementalMesh(s,0.05,False,0.5,True)
    ex=TopExp_Explorer(s,TopAbs_FACE)
    while ex.More():
        f=TopoDS.Face_s(ex.Current()); loc=TopLoc_Location()
        tri=BRep_Tool.Triangulation_s(f,loc)
        if tri is not None:
            tr=loc.Transformation()
            P=np.array([[tri.Node(i).Transformed(tr).X(),tri.Node(i).Transformed(tr).Y(),tri.Node(i).Transformed(tr).Z()] for i in range(1,tri.NbNodes()+1)])
            rev=f.Orientation()==TopAbs_REVERSED
            for i in range(1,tri.NbTriangles()+1):
                a,b,cc=tri.Triangle(i).Get()
                t=P[[a-1,cc-1,b-1]] if rev else P[[a-1,b-1,cc-1]]
                if obj.startswith('Part__Feature'): t=t+np.array([13.3,-25.3,-0.635])
                tris.append(t); cols.append(c)
        ex.Next()
T=np.array(tris); C=np.array(cols); print(T.shape)
np.save('T.npy',T); np.save('C.npy',C)
print(T.reshape(-1,3).min(0),T.reshape(-1,3).max(0))
