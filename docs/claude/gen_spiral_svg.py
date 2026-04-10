import math, os

cx, cy = 270, 235
spacing = 46.0
c = spacing / math.sqrt(2 * math.pi)
rMin = 22.0
rMax = 140.0
goldenAngle = math.pi * (3 - math.sqrt(5))
thetaMin = (rMin / c) ** 2
thetaMax = (rMax / c) ** 2

def spiral_xy(theta, arm_index, inward=False):
    r = c * math.sqrt(theta)
    if not inward:
        angle = theta + arm_index * goldenAngle
    else:
        psi = 2.0 * thetaMax + arm_index * goldenAngle
        angle = -theta + psi
    return cx + r * math.cos(angle), cy + r * math.sin(angle)

def make_path(pts):
    d = "M %.2f,%.2f" % pts[0]
    for x, y in pts[1:]:
        d += " L %.2f,%.2f" % (x, y)
    return d

N = 400
colors_out = ["#58a6ff", "#3fb950", "#f78166", "#c792ea"]
colors_in  = ["#3d7dc8", "#2d8a3e", "#a04030", "#8060b8"]

arm_paths = []
for arm in range(4):
    out_pts = [spiral_xy(thetaMin + (thetaMax - thetaMin)*i/N, arm, False) for i in range(N+1)]
    in_pts  = [spiral_xy(thetaMin + (thetaMax - thetaMin)*i/N, arm, True)  for i in range(N+1)]
    in_pts.reverse()
    arm_paths.append((make_path(out_pts), colors_out[arm], make_path(in_pts), colors_in[arm]))

lines = []
def L(s=""): lines.append(s)

L('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 620 490" width="620" height="490">')
L('  <style>text { font-family: ui-monospace, SFMono-Regular, monospace; }</style>')
L('  <defs>')
L('    <marker id="arr-gold" markerWidth="7" markerHeight="7" refX="5" refY="3" orient="auto">')
L('      <path d="M0,0 L0,6 L7,3 z" fill="#e6a817"/>')
L('    </marker>')
L('  </defs>')
L()
L('  <!-- Background -->')
L('  <rect width="620" height="490" fill="#0d1117"/>')
L()
L('  <!-- ROI rectangle -->')
L('  <rect x="%d" y="%d" width="%d" height="%d" fill="none" stroke="#444" stroke-width="1.5" stroke-dasharray="6,4"/>' %
  (cx-rMax, cy-rMax, 2*rMax, 2*rMax))
L('  <text x="%d" y="%d" fill="#555" text-anchor="middle" font-size="11">ROI rectangle</text>' %
  (cx, cy-rMax-8))
L()
L('  <!-- Outer radius circle -->')
L('  <circle cx="%d" cy="%d" r="%.0f" fill="none" stroke="#3a6fa0" stroke-width="1.2" stroke-dasharray="7,4"/>' %
  (cx, cy, rMax))
L()
L('  <!-- Min radius circle -->')
L('  <circle cx="%d" cy="%d" r="%.0f" fill="none" stroke="#e6a817" stroke-width="1.4" stroke-dasharray="4,3"/>' %
  (cx, cy, rMin))
L()
L('  <!-- Center dot -->')
L('  <circle cx="%d" cy="%d" r="3" fill="#e6a817"/>' % (cx, cy))
L()
L('  <!-- Spiral arms (outward then inward for each of 4 arm cycles) -->')
for d_out, c_out, d_in, c_in in arm_paths:
    L('  <path d="%s" fill="none" stroke="%s" stroke-width="1.6" opacity="0.9"/>' % (d_out, c_out))
    L('  <path d="%s" fill="none" stroke="%s" stroke-width="1.3" opacity="0.75"/>' % (d_in, c_in))
L()

# R_max annotation
angle45 = math.pi * 0.72
L('  <!-- R_max annotation -->')
L('  <line x1="%d" y1="%d" x2="%.0f" y2="%.0f" stroke="#3a6fa0" stroke-width="1" stroke-dasharray="3,3"/>' %
  (cx, cy, cx + rMax*math.cos(angle45), cy - rMax*math.sin(angle45)))
L('  <text x="%.0f" y="%.0f" fill="#3a6fa0" font-size="11">R_max = min(w,h)/2</text>' %
  (cx + rMax*math.cos(angle45)+4, cy - rMax*math.sin(angle45)-4))
L('  <text x="%.0f" y="%.0f" fill="#3a6fa0" font-size="10">(inscribed circle, auto)</text>' %
  (cx + rMax*math.cos(angle45)+4, cy - rMax*math.sin(angle45)+8))

# rMin annotation
L('  <!-- rMin annotation -->')
L('  <line x1="%d" y1="%d" x2="%.0f" y2="%.0f" stroke="#e6a817" stroke-width="1" stroke-dasharray="3,3"/>' %
  (cx, cy, cx - rMin*0.6, cy - rMin*0.8))
L('  <text x="%.0f" y="%.0f" fill="#e6a817" text-anchor="end" font-size="11">rMin (inner cutoff)</text>' %
  (cx - rMin*0.6 - 3, cy - rMin*0.8))

# Turn spacing annotation
t_a = thetaMin + 2 * math.pi * 3
t_b = t_a + 2 * math.pi
if t_b < thetaMax:
    pa = spiral_xy(t_a, 0, False)
    pb = spiral_xy(t_b, 0, False)
    mx, my = (pa[0]+pb[0])/2, (pa[1]+pb[1])/2
    L('  <!-- Turn spacing annotation -->')
    L('  <circle cx="%.2f" cy="%.2f" r="2.5" fill="#aaa"/>' % pa)
    L('  <circle cx="%.2f" cy="%.2f" r="2.5" fill="#aaa"/>' % pb)
    L('  <line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#aaa" stroke-width="1.1" stroke-dasharray="3,3"/>' % (pa+pb))
    L('  <text x="%.0f" y="%.0f" fill="#aaa" text-anchor="middle" font-size="10">turn spacing</text>' % (mx, my-7))

# offset(X) arrow
L('  <!-- offset(X) annotation -->')
L('  <line x1="%.0f" y1="%d" x2="%d" y2="%d" stroke="#e6a817" stroke-width="1.3" stroke-dasharray="4,3" marker-end="url(#arr-gold)"/>' %
  (cx-58, cy, cx-4, cy))
L('  <text x="%.0f" y="%d" fill="#e6a817" text-anchor="end" font-size="11">offset(X)</text>' % (cx-62, cy-5))
L('  <text x="%.0f" y="%d" fill="#e6a817" text-anchor="end" font-size="10">shifts center H</text>' % (cx-62, cy+8))

# Golden angle arc
p0 = spiral_xy(thetaMin, 0, False)
p1 = spiral_xy(thetaMin, 1, False)
a0 = math.atan2(p0[1]-cy, p0[0]-cx)
a1 = math.atan2(p1[1]-cy, p1[0]-cx)
arc_r = rMin + 12
ax0 = cx + arc_r * math.cos(a0)
ay0 = cy + arc_r * math.sin(a0)
ax1 = cx + arc_r * math.cos(a1)
ay1 = cy + arc_r * math.sin(a1)
# Large arc flag: goldenAngle > pi? No, 2.4 < pi, so 0
L('  <!-- Golden angle arc -->')
L('  <path d="M %.2f,%.2f A %.1f,%.1f 0 0,1 %.2f,%.2f" fill="none" stroke="#c792ea" stroke-width="1.3"/>' %
  (ax0, ay0, arc_r, arc_r, ax1, ay1))
midAng = (a0 + a1) / 2
L('  <text x="%.0f" y="%.0f" fill="#c792ea" font-size="10" text-anchor="middle">golden angle ~137.5°</text>' %
  (cx + (arc_r+18)*math.cos(midAng), cy + (arc_r+18)*math.sin(midAng)))

# --- Legend box ---
lx, ly = 452, 22
L()
L('  <!-- Legend box -->')
L('  <rect x="%d" y="%d" width="158" height="172" rx="6" fill="#161b22" stroke="#30363d" stroke-width="1"/>' % (lx, ly))
L('  <text x="%d" y="%d" fill="#ccc" text-anchor="middle" font-size="12" font-weight="bold">Arm Cycle</text>' % (lx+79, ly+18))

items = [
    (colors_out[0], False, "1. Outward  rMin -&gt; R_max"),
    ("#aaa",        True,  "2. Periph spline (128 samp)"),
    (colors_in[0],  False, "3. Inward   R_max -&gt; rMin"),
    ("#aaa",        True,  "4. Center spline (variable)"),
]
for i, (col, dashed, label) in enumerate(items):
    yy = ly + 36 + i*22
    dash = ' stroke-dasharray="4,3"' if dashed else ""
    L('  <line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s" stroke-width="1.8"%s/>' %
      (lx+10, yy, lx+35, yy, col, dash))
    L('  <text x="%d" y="%d" fill="%s" font-size="10">%s</text>' % (lx+42, yy+4, col, label))

notes = ["Each color = one arm cycle.", "Arms rotate by golden angle.", "Sample rate fixed at 100 kHz."]
for i, note in enumerate(notes):
    L('  <text x="%d" y="%d" fill="#666" font-size="10">%s</text>' % (lx+10, ly+128+i*14, note))

# --- Parameter box ---
px, py2 = 452, 210
L()
L('  <!-- Parameter box -->')
L('  <rect x="%d" y="%d" width="158" height="193" rx="6" fill="#161b22" stroke="#30363d" stroke-width="1"/>' % (px, py2))
L('  <text x="%d" y="%d" fill="#ccc" text-anchor="middle" font-size="12" font-weight="bold">Parameters</text>' % (px+79, py2+18))

params = [
    ("#e6a817", "rMin",          "inner cutoff (px)"),
    ("#aaa",    "turn spacing",  "arm density (px)"),
    ("#aaa",    "turn duration", "ms per revolution"),
    ("#e6a817", "offset(X)",     "center shift (px)"),
]
for i, (col, name, desc) in enumerate(params):
    yy = py2 + 38 + i*20
    L('  <text x="%d" y="%d" fill="%s" font-size="11">%s</text>' % (px+10, yy, col, name))
    L('  <text x="%d" y="%d" fill="#666" font-size="10" text-anchor="end">%s</text>' % (px+148, yy, desc))

L('  <line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#30363d" stroke-width="1"/>' % (px+10, py2+120, px+148, py2+120))
formulas = [
    "r = c · √θ",
    "c = spacing / √(2π)",
    "θ_max = (R_max / c)²",
    "θ_min = (rMin  / c)²",
    "n_turns = R² / spacing²",
]
for i, f in enumerate(formulas):
    L('  <text x="%d" y="%d" fill="#555" font-size="10">%s</text>' % (px+10, py2+135+i*13, f))

L('</svg>')

out_path = os.path.join(os.path.dirname(__file__), "fermat-spiral-scan.svg")
with open(out_path, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("Written:", out_path)
