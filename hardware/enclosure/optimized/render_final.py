"""Eight production-geometry views, 1600 x 1200 pixels."""
import argparse
from build import ROOT,OUT,run
ap=argparse.ArgumentParser()
ap.add_argument('--views',nargs='*')
args=ap.parse_args()
views={
 'front_left':('assembly','-400,-550,350,0,0,95'),
 'front_right':('assembly','400,-550,350,0,0,95'),
 'side':('assembly','650,0,95,0,0,95'),
 'rear':('assembly','400,550,350,0,0,95'),
 'bottom':('assembly','300,-420,-450,0,0,80'),
 'cutaway':('cutaway','450,-900,470,-50,0,90'),
 'base_internal':('base_internal','140,-280,420,0,0,30'),
 'exploded':('exploded','450,-700,400,0,0,110'),
}
for name in args.views or views:
 mode,camera=views[name]
 run(r'C:\Program Files\OpenSCAD\openscad.exe','final_'+name,
     ['-o',OUT/('final_'+name+'.png'),'--imgsize=1600,1200',
      '--projection=o','--colorscheme=Tomorrow','--camera='+camera,
      '-D','MESH_PREVIEW=true','-D','CAMERA_INSTALLED=true','-D',f'VIEW="{mode}"',ROOT/'presentation.scad'])
