import functions_framework
import matplotlib
# Force headless backend
matplotlib.use('Agg') 
import matplotlib.pyplot as plt
import io
from flask import send_file

# --- OUTPUT SHAPE TOGGLE (hardcoded for now; not yet a request/config parameter) ---
# False: current behavior. Output is ~4:3 — bbox_inches='tight' trims to the AXES BOX (not
#        to the drawn content), and set_aspect('equal', adjustable='datalim') below pins
#        that box at matplotlib's default proportions (4.96 x 3.696 in = 1.342), padding
#        the data limits instead. Shape is therefore constant whatever the scene is.
# True:  force a 1:1 output by squaring the axes box itself, which is what the tight bbox
#        actually measures. Data still renders at true 'equal' aspect, so non-square scenes
#        gain whitespace bands rather than distorting.
FORCE_SQUARE_CANVAS = False

@functions_framework.http
def render_plot(request):
    request_json = request.get_json(silent=True)
    
    # Guard clause
    if not request_json or 'code' not in request_json:
        return 'No code provided', 400

    code_to_run = request_json['code']
    img_buf = io.BytesIO()
    
    try:
        # 1. Clear all existing plots to prevent container memory leaks
        plt.close('all')
        
        # 2. Create a clean local namespace for execution
        local_scope = {}
        
        # 3. EXECUTE THE LLM CODE
        exec(code_to_run, globals(), local_scope)
        
        # --- SYSTEM POST-PROCESSING SAFEGUARDS ---
        
        # Grab the current figure the LLM just worked on
        current_fig = plt.gcf()
        axes = current_fig.get_axes()
        
        # Fallback if the LLM somehow plotted without explicitly creating an axis
        if not axes:
            axes = [plt.gca()]

        for ax in axes:
            # Strip all axes, grids, borders, and ticks
            ax.axis('off')

            # Add a small 5% internal margin so shapes don't scrape the edge of the data limits
            ax.margins(0.05)
            
            # Enforce strict mathematical aspect ratio safely
            try:
                # Check if it's a 3D projection
                if hasattr(ax, 'get_zaxis'):
                    ax.set_box_aspect([1, 1, 1])
                # Otherwise, handle as standard 2D
                else:
                    ax.autoscale(enable=True, tight=False)
                    ax.set_aspect('equal', adjustable='datalim')
            except ValueError:
                # Failsafe if the axis is completely empty or corrupted
                pass

            # Square-output toggle (2D only; 3D already gets a cubic box above)
            if FORCE_SQUARE_CANVAS and not hasattr(ax, 'get_zaxis'):
                ax.set_box_aspect(1)
        
        # Save the result to the buffer with a transparent background
        plt.savefig(
            img_buf, 
            format='png', 
            bbox_inches='tight', 
            pad_inches=0, 
            transparent=False
        )
        img_buf.seek(0)
        
        # Return the binary image
        return send_file(img_buf, mimetype='image/png')
        
    except Exception as e:
        # Catch and return the exact Python traceback so the Phase 3 Reviewer agent can fix it
        return f"Error executing code: {str(e)}", 500