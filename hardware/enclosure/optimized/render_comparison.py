"""Same camera/scale/colours; two actual production mesh assemblies in OpenSCAD."""
from build import ROOT,OUT,run
run(r'C:\Program Files\OpenSCAD\openscad.exe','comparison_66mm_vs_optimized',[
    '-o',OUT/'comparison_66mm_vs_optimized.png','--imgsize=2000,1200',
    '--projection=o','--colorscheme=Tomorrow','--camera=0,-950,380,0,0,85',ROOT/'comparison.scad'])
