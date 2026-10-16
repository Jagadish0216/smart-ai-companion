"""One-time source migration. Historical inputs are archived in history/."""
from pathlib import Path
import re
R=Path(__file__).resolve().parent
p=R/'dimensions.scad'; s=p.read_text()
values={
'base_w':'174','base_d':'114','base_h':'66','base_r':'16',
'base_cover_points':'[[-76,-46],[76,-46],[-76,46],[12,46]]',
'base_boss_d':'9','base_boss_h':'9','base_boss_ramp_h':'12',
'pi_origin':'[80,-32,floor_t+7]','cooler_size':'[70,50,22]',
'cooler_origin':'[1,5,pcb_t+3]',
'pi_cavity_min':'[22,-40,3]','pi_cavity_size':'[62,94,60]',
'rear_port_x':'[20,85]','exhaust_cut_depth':'16',
'speaker_bay':'[78,78,40]','speaker_xy':'[-38,0]',
'speaker_reserved_z':'17','speaker_mount_size':'[86,86,3]',
'speaker_mount_z':'11.5','speaker_mount_open':'[54,54]',
'speaker_mount_points':'[[-37,-37],[37,-37],[-37,37],[37,37]]',
'speaker_mount_boss_d':'7','speaker_slot_l':'23','speaker_side_slot_l':'23',
'divider_x':'8','divider_t':'3','divider_y':'[-28,38]','divider_top':'58',
'amp_xy':'[-38,-39.5]','amp_z':'7','led_z':'27',
'foot_h':'10','foot_points':'[[-69,-46],[69,-46],[-69,46],[69,46]]',
'base_tie_points':'[[16,-41],[15,28]]',
'service_reserve_min':'[3,-27,20]','service_reserve_size':'[20,23,35]',
'col_shell':'[0.93,0.92,0.88]',
}
for key,val in values.items():
 s,n=re.subn(r'^'+re.escape(key)+r'\s*=.*?;',key+' = '+val+';',s,flags=re.M)
 assert n==1,key
s+='''
// Final down-firing revision. Frozen head datum is independent of base height.
head_mount_datum = 65;
head_assembly_shift = base_h-head_mount_datum;
base_top_w = 160;
base_top_d = 102;
base_taper_h = base_h-floor_t;
speaker_gasket_t = 2.5;
speaker_cone_d = 40;
speaker_acoustic_keepout_d = 50;
speaker_grille_rows = [-22,-16.5,-11,-5.5,0,5.5,11,16.5,22];
speaker_bottom_slot_w = 3;
speaker_grille_radius = 29;
mic_round_pocket_d = 27;
mic_round_board_d = 26;
mic_round_y = -39.5;
mic_adapter_y = -41.5;
tact_switch_size = [6,6,5]; // verification envelope, actual switch must be measured
tact_body_x = 54;
tact_contact_x = 59;
button_actuator_d = 11.5;
button_actuator_flange_d = 15;
button_travel = 0.8;
'''
p.write_text(s)
# Freeze the original head/neck math, while translating the whole head +1 mm in assembly.
p=R/'head_shell.scad'; s=p.read_text().replace('base_h','head_mount_datum')
s=s.replace('neck_cable_cut(head_mount_datum-eps,head_mount_datum+25);','neck_cable_cut(head_mount_datum-eps,head_mount_datum+25,head_mount_datum);')
p.write_text(s)
p=R/'utilities.scad'; s=p.read_text().replace('module neck_cable_cut(z0,z1)', 'module neck_cable_cut(z0,z1,flare_z=base_h)').replace('base_h-cable_flare','flare_z-cable_flare')
s+='''
module assembly_head_frame() { translate([0,0,head_assembly_shift]) head_frame() children(); }
function base_width_at(z) = base_w-(base_w-base_top_w)*max(0,z-floor_t)/base_taper_h;
function base_depth_at(z) = base_d-(base_d-base_top_d)*max(0,z-floor_t)/base_taper_h;
module tapered_base_volume(z0,z1,inset=0) {
    hull() for(z=[z0,z1]) translate([0,0,z])
        rr(base_width_at(z)-2*inset,base_depth_at(z)-2*inset,base_r-inset,eps);
}
module bottom_speaker_slots(h) {
    for(y=speaker_grille_rows)
        translate([speaker_xy[0],speaker_xy[1]+y,0])
            slot(2*sqrt(speaker_grille_radius*speaker_grille_radius-y*y),speaker_bottom_slot_w,h);
}
'''
p.write_text(s)
