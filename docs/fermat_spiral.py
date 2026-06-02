"""
Fermat's Spiral — Interactive Galvo XY Plotter
Requires: numpy, matplotlib
  pip install numpy matplotlib
"""

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.widgets import Slider, CheckButtons


def compute_spiral(turns, a, N, dual=True):
    theta_max = turns * 2 * np.pi
    theta = np.linspace(0, theta_max, N)
    r_pos = a * np.sqrt(theta)
    x_pos = r_pos * np.cos(theta)
    y_pos = r_pos * np.sin(theta)
    if dual:
        r_neg = -a * np.sqrt(theta)
        x_neg = r_neg * np.cos(theta)
        y_neg = r_neg * np.sin(theta)
    else:
        x_neg, y_neg = None, None
    return theta, x_pos, y_pos, x_neg, y_neg


# --- Initial params ---
TURNS_INIT = 4.0
SCALE_INIT = 1.5
N_INIT     = 800
DUAL_INIT  = True

fig = plt.figure(figsize=(12, 7), facecolor='#1a1a1a')
fig.canvas.manager.set_window_title("Fermat's Spiral — Galvo XY")

gs = gridspec.GridSpec(
    3, 2,
    figure=fig,
    left=0.07, right=0.97,
    top=0.93, bottom=0.28,
    hspace=0.45, wspace=0.35
)

ax_spiral = fig.add_subplot(gs[:, 0], facecolor='#111')
ax_x      = fig.add_subplot(gs[0, 1], facecolor='#111')
ax_y      = fig.add_subplot(gs[1, 1], facecolor='#111')
ax_phase  = fig.add_subplot(gs[2, 1], facecolor='#111')

for ax in [ax_spiral, ax_x, ax_y, ax_phase]:
    ax.tick_params(colors='#888', labelsize=8)
    for sp in ax.spines.values():
        sp.set_color('#333')

def style_ax(ax, title):
    ax.set_title(title, color='#aaa', fontsize=9, pad=4)
    ax.axhline(0, color='#333', lw=0.5)
    ax.grid(True, color='#222', lw=0.4)
    ax.set_xlabel('sample index', color='#666', fontsize=7)

# Draw initial spiral
theta, xp, yp, xn, yn = compute_spiral(TURNS_INIT, SCALE_INIT, N_INIT, DUAL_INIT)

line_pos, = ax_spiral.plot(xp, yp, color='#378ADD', lw=1.2, label='+r')
line_neg, = ax_spiral.plot(xn if xn is not None else [], yn if yn is not None else [],
                            color='#D85A30', lw=1.2, label='−r')
ax_spiral.set_aspect('equal')
ax_spiral.set_title("Spiral  (galvo XY plane)", color='#aaa', fontsize=9, pad=4)
ax_spiral.axhline(0, color='#333', lw=0.5)
ax_spiral.axvline(0, color='#333', lw=0.5)
ax_spiral.grid(True, color='#222', lw=0.4)
ax_spiral.tick_params(colors='#888', labelsize=8)
ax_spiral.legend(fontsize=8, facecolor='#1a1a1a', labelcolor='#aaa', framealpha=0.6)

# Center dot
center_dot, = ax_spiral.plot([0], [0], 'o', color='#555', ms=4, zorder=5)

# X waveform
lx, = ax_x.plot(xp, color='#378ADD', lw=1.0)
style_ax(ax_x, 'Galvo X  (= r·cos θ)')
ax_x.set_ylabel('amplitude', color='#666', fontsize=7)

# Y waveform
ly, = ax_y.plot(yp, color='#D85A30', lw=1.0)
style_ax(ax_y, 'Galvo Y  (= r·sin θ)')
ax_y.set_ylabel('amplitude', color='#666', fontsize=7)

# Phase portrait (X vs Y — same as spiral, useful for verification)
lxy, = ax_phase.plot(xp, yp, color='#9F7FDD', lw=0.8, alpha=0.8)
ax_phase.set_aspect('equal')
ax_phase.set_title('Phase portrait  X vs Y', color='#aaa', fontsize=9, pad=4)
ax_phase.grid(True, color='#222', lw=0.4)
ax_phase.axhline(0, color='#333', lw=0.5)
ax_phase.axvline(0, color='#333', lw=0.5)
ax_phase.set_xlabel('X', color='#666', fontsize=7)
ax_phase.set_ylabel('Y', color='#666', fontsize=7)

# --- Sliders ---
slider_color   = '#2a2a2a'
slider_fc      = '#1a1a1a'

ax_s_turns = plt.axes([0.10, 0.20, 0.55, 0.025], facecolor=slider_color)
ax_s_scale = plt.axes([0.10, 0.15, 0.55, 0.025], facecolor=slider_color)
ax_s_npts  = plt.axes([0.10, 0.10, 0.55, 0.025], facecolor=slider_color)

s_turns = Slider(ax_s_turns, 'Turns', 0.5, 10.0, valinit=TURNS_INIT, valstep=0.5,
                 color='#378ADD')
s_scale = Slider(ax_s_scale, 'Scale (a)', 0.3, 4.0, valinit=SCALE_INIT, valstep=0.1,
                 color='#378ADD')
s_npts  = Slider(ax_s_npts,  'Points N',  100, 3000, valinit=N_INIT, valstep=100,
                 color='#378ADD')

for sl in [s_turns, s_scale, s_npts]:
    sl.label.set_color('#aaa')
    sl.valtext.set_color('#aaa')

# --- Checkbox ---
ax_chk = plt.axes([0.77, 0.08, 0.18, 0.14], facecolor='#1a1a1a')
chk = CheckButtons(ax_chk, ['Both branches'], [DUAL_INIT])
chk.labels[0].set_color('#aaa')
chk.labels[0].set_fontsize(9)


def update(_=None):
    turns = s_turns.val
    a     = s_scale.val
    N     = int(s_npts.val)
    dual  = chk.get_status()[0]

    theta, xp, yp, xn, yn = compute_spiral(turns, a, N, dual)

    line_pos.set_data(xp, yp)
    if dual and xn is not None:
        line_neg.set_data(xn, yn)
    else:
        line_neg.set_data([], [])

    r = a * np.sqrt(turns * 2 * np.pi)
    lim = r * 1.1
    ax_spiral.set_xlim(-lim, lim)
    ax_spiral.set_ylim(-lim, lim)

    lx.set_data(np.arange(N), xp)
    ax_x.set_xlim(0, N)
    ax_x.set_ylim(-r * 1.05, r * 1.05)

    ly.set_data(np.arange(N), yp)
    ax_y.set_xlim(0, N)
    ax_y.set_ylim(-r * 1.05, r * 1.05)

    lxy.set_data(xp, yp)
    ax_phase.set_xlim(-lim, lim)
    ax_phase.set_ylim(-lim, lim)

    fig.canvas.draw_idle()


s_turns.on_changed(update)
s_scale.on_changed(update)
s_npts.on_changed(update)
chk.on_clicked(update)

update()

# --- Export helper ---
def export_coords(turns, a, N):
    """Return (x, y) arrays normalized to [-1, 1] for DAC output."""
    theta_max = turns * 2 * np.pi
    theta = np.linspace(0, theta_max, N)
    r = a * np.sqrt(theta)
    x = r * np.cos(theta)
    y = r * np.sin(theta)
    rmax = np.max(np.abs([x, y]))
    return x / rmax, y / rmax


fig.text(0.77, 0.22, 'export_coords(turns, a, N)',
         color='#555', fontsize=7, family='monospace')
fig.text(0.77, 0.19, '→ (x,y) in [−1,1] for DAC',
         color='#555', fontsize=7)

plt.suptitle("Fermat's Spiral  —  Galvo XY Plotter", color='#ccc', fontsize=11, y=0.98)
plt.show()
