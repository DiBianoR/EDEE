import functions_framework
import matplotlib
# Force headless backend
matplotlib.use('Agg') 
import matplotlib.pyplot as plt
import io
from flask import send_file

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