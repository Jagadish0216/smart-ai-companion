"""One-time Option B setup, run only after the immutable 66 mm backup exists."""
from pathlib import Path
R=Path(__file__).resolve().parent/'optimized'
def edit(name, pairs):
    p=R/name;s=p.read_text(encoding='utf-8')
    for a,b in pairs:
        assert a in s,(name,a)
        s=s.replace(a,b)
    p.write_text(s,encoding='utf-8')
edit('dimensions.scad',[
 ('base_h = 66;', 'base_h = 62;\nbase_wall = 1.8;\nbase_panel_t = 1.8;\nbase_roof_t = 1.8;'),
 ('base_boss_d = 9;', 'base_boss_d = 8;'),
 ('base_boss_h = 9;', 'base_boss_h = 8;'),
 ('speaker_reserved_z = 13.5;', 'speaker_reserved_z = 10.8;'),
 ('speaker_mount_size = [86,86,3];','speaker_mount_size = [86,86,2.2];'),
 ('speaker_mount_z = 8;', 'speaker_mount_z = 6.1;'),
 ('divider_t = 3;', 'divider_t = 1.6;'),
 ('divider_top = 54;', 'divider_top = 50;'),
 ('amp_shelf = [32,28,3];','amp_shelf = [32,28,2.2];'),
])
edit('base_shell.scad',[
 ('base_h-floor_t,wall','base_h-base_roof_t,base_wall'),
 ('/2-wall','/2-base_wall'),('d=wall','d=base_wall'),
 ('            for(p=base_cover_points) {','''            // Local 3 mm neck land and roof ribs connect the load to the skin.
            translate([0,neck_y,base_h-floor_t]) rr(62,46,8,floor_t);
            translate([-80,neck_y-0.8,base_h-floor_t]) cube([160,1.6,floor_t]);
            translate([-0.8,-51,base_h-floor_t]) cube([1.6,102,floor_t]);
            // Local port-edge bands, clipped to the accepted outside taper.
            intersection() {
                difference() {
                    tapered_base_volume(floor_t,base_h-eps);
                    tapered_base_volume(floor_t-eps,base_h-base_roof_t,2.6);
                }
                union() {
                    translate([18,39,rear_port_top]) cube([69,20,3]);
                    translate([69,right_port_y[0]-3,right_port_top]) cube([20,58,3]);
                }
            }
            for(p=base_cover_points) {'''),
])
edit('base_bottom_cover.scad',[
 ('rr(base_w,base_d,base_r,floor_t);','''rr(base_w,base_d,base_r,base_panel_t);
            // 3 mm local perimeter, feet and mount lands; no full thick panel.
            difference() {
                rr(base_w,base_d,base_r,floor_t);
                translate([0,0,-eps]) rr(base_w-10,base_d-10,base_r-5,floor_t+2*eps);
            }
            for(p=foot_points) translate([p[0],p[1],0]) cylinder(d=foot_d+3,h=floor_t);
            for(p=base_cover_points) translate([p[0],p[1],0]) cylinder(d=11,h=floor_t);
            pi_frame() for(p=pi_holes) translate([p[0],p[1],-pi_origin[2]]) cylinder(d=9,h=floor_t);
            for(p=speaker_mount_points) translate([speaker_xy[0]+p[0],speaker_xy[1]+p[1],0]) cylinder(d=10,h=floor_t);
            // Sparse 1.6 mm ribs, outside the acoustic circle and Pi intake.
            for(y=[-37,37]) translate([-75,y-0.8,base_panel_t-eps]) cube([74,1.6,3+eps]);
            for(x=[-75,-1]) translate([x-0.8,-37,base_panel_t-eps]) cube([1.6,74,3+eps]);
            translate([8,-43,base_panel_t-eps]) cube([1.6,86,3+eps]);
            for(y=[-29,29.5]) translate([26,y-0.8,base_panel_t-eps]) cube([54,1.6,3+eps]);'''),
 ('wall+fit_normal','base_wall+fit_normal'),
 ('translate([amp_xy[0],amp_panel_y,floor_t+amp_shelf[1]/2]) rotate([-90,0,0]) rr(amp_shelf[0],amp_shelf[1],2,-0+amp_shelf[2]);', '''translate([amp_xy[0],amp_panel_y,base_panel_t+amp_shelf[1]/2]) rotate([-90,0,0]) difference() {
                rr(amp_shelf[0],amp_shelf[1],2,amp_shelf[2]);
                translate([0,0,-eps]) rr(17,20,2,amp_shelf[2]+2*eps);
            }'''),
 ('translate([divider_x,divider_y[0],floor_t-eps]) cube([divider_t,divider_y[1]-divider_y[0],divider_top-floor_t+eps]);', 'translate([divider_x,divider_y[0],base_panel_t-eps]) cube([divider_t,divider_y[1]-divider_y[0],divider_top-base_panel_t+eps]);'),
 ('translate([6,-43,floor_t-eps]) cube([3,86,3+eps]);','// The narrow divider-base rib above replaces the former 3 mm solid rib.'),
 ('cube([divider_t,wall,16])','cube([divider_t,1.6,16])'),
 ('cube([8+divider_t,wall,wall])','cube([8+divider_t,1.6,base_panel_t])'),
 ('translate([divider_x,y,floor_t])','translate([divider_x,y,base_panel_t-eps])'),
 ('for(p=base_tie_points) translate([p[0],p[1],floor_t-eps])','for(p=base_tie_points) translate([p[0],p[1],base_panel_t-eps])'),
])
edit('speaker_mount.scad',[
 ('rr(speaker_mount_size[0],speaker_mount_size[1],4,speaker_mount_size[2]);', '''union() {
            // Outer frame, acoustic ring and four diagonal load-transfer webs.
            difference() {
                rr(86,86,4,speaker_mount_size[2]);
                translate([0,0,-eps]) rr(64,64,5,speaker_mount_size[2]+2*eps);
            }
            difference() {
                cylinder(d=60,h=speaker_mount_size[2]);
                translate([0,0,-eps]) cylinder(d=54,h=speaker_mount_size[2]+2*eps);
            }
            for(a=[45:90:315]) rotate([0,0,a]) translate([27,-2,0]) cube([24,4,speaker_mount_size[2]]);
            for(p=speaker_mount_points) translate([p[0],p[1],0]) cylinder(d=10,h=2.8);
        }'''),
 ('h=speaker_mount_size[2]+2*eps','h=3+2*eps'),
 ('speaker_mount_size[2]-1.7','2.8-1.7'),
])
edit('clearance_checks.scad',[
 ('assert(base_h==66 && base_w==174 && base_d==114);','''assert(base_h>=62 && base_h<=64 && base_w==174 && base_d==114);
    assert(base_top_w==160 && base_top_d==102);
    assert(base_wall>=1.6 && base_panel_t>=1.6);
    assert(speaker_reserved_z-base_panel_t>=7);
    assert(speaker_reserved_z==speaker_mount_z+speaker_mount_size[2]+speaker_gasket_t);'''),
 ('floor_t+eps]) cylinder(d=speaker_acoustic_keepout_d,h=speaker_reserved_z-floor_t-2*eps)', 'base_panel_t+eps]) cylinder(d=speaker_acoustic_keepout_d,h=speaker_reserved_z-base_panel_t-2*eps)'),
])
# All nine head-related production exports must match the immediately preserved Option A.
edit('build.py',[
 ('FROZEN_PARTS = {"head_shell", "head_rear_cover", "front_bezel", "camera_blank_insert", "camera_lens_insert", "camera_carrier"}', 'FROZEN_PARTS = set(PARTS)-{"base_shell", "base_bottom_cover", "universal_speaker_mount"}'),
 ("ROOT/'history/before_downfiring_revision.zip'", "ROOT.parent/'output/archive/Smart_Companion_66mm_Original_Backup.zip'"),
])
print('Prepared isolated 62 mm design; original CAD untouched.')
