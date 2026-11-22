# Items for the next meeting

Weekly meeting: Tuesdays. My agenda for the meeting: anything to ask about, mention or show.
This is separate from the to-do list (`to-do list.txt`), though an item can be in both.
Add things as they come up; clear or move them after the meeting.

## To ask

- **Light or dark theme by default?** The frontend already has Streamlit's built-in light/dark switch (top-right ⋮ menu → Settings). Should we set one as the default, or leave it following the viewer's system setting?
- **How long should finished jobs be kept?** Since the image-versions change, the bucket is the only record of a job once its Firestore doc expires, and the bucket's lifecycle rule deletes everything after 360 days. Is a year enough, or should old jobs live longer?

## To mention or show

- **Every image version is kept** (Candace 11/17: use versions the AI rejected). In a finished job, the carousel's "Show all versions" toggle lists every scaffold and illustration, each marked with what the reviewers decided, and each can be downloaded. Choosing a non-final version as the starting point for a resume isn't built yet.
- Bigger, clearer fonts throughout the frontend (Candace 11/17), plus the before/after comparison images in `notes/`.
