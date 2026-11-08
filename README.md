# Photo Sorter

Sorts a folder of unsorted photos into folders named `yyyymm-name`, with ratings and keywords that Lightroom Classic can read. Everything runs on your own computer: the app opens in your browser, but it only talks to a small program running on the same computer. No photo, file name or metadata is sent anywhere.

Version 1.0.0

---

## 1. Install (once)

Tested setup: iMac 2017, macOS 13 Ventura. The same steps work on newer Macs.

### 1.1 Install Python

1. Open <https://www.python.org/downloads/macos/>.
2. Under **Python 3.13** (latest 3.13.x release), click **macOS 64-bit universal2 installer** to download the `.pkg` file.
3. Double-click the downloaded file and follow the installer (Continue, Agree, Install).
4. When it finishes, a Finder window opens. You can close it. You do **not** need "Install Certificates" for Photo Sorter.

### 1.2 Install ExifTool

ExifTool reads dates, cameras and GPS from photos, and writes keywords and ratings.

1. Open <https://exiftool.org>.
2. Click the **macOS Package** link (`ExifTool-13.xx.pkg`).
3. Double-click the downloaded file. If macOS says it cannot be opened, right-click it, choose **Open**, then **Open** again.
4. Follow the installer.

### 1.3 Check both

1. Open **Terminal** (Applications › Utilities › Terminal).
2. Type each line and press Enter:

   ```
   python3 --version
   exiftool -ver
   ```

3. You should see `Python 3.13.x` and a number such as `13.xx`. If either says "command not found", repeat that install step.

### 1.4 Put Photo Sorter in place

1. Unzip `PhotoSorter.zip` (double-click it).
2. Move the `PhotoSorter` folder to your home folder (in Finder: Go › Home), so it sits at `/Users/<you>/PhotoSorter`.

### 1.5 First start

1. Open the `PhotoSorter` folder.
2. **Right-click** `Start Photo Sorter.command` and choose **Open**. macOS warns that it comes from an unidentified developer. Click **Open**. You only do this the first time; afterwards a double-click is enough.
3. A Terminal window opens and your browser shows Photo Sorter.
4. When you first choose a folder on the Desktop, in Documents or Downloads, or on an SD card or external drive, macOS asks whether Terminal may access it. Click **Allow** (or OK).

If double-clicking ever shows "permission denied", open Terminal and run:

```
chmod +x ~/PhotoSorter/"Start Photo Sorter.command"
```

You can also always start it from Terminal:

```
cd ~/PhotoSorter
python3 -m photosorter
```

---

## 2. Use it

Photo Sorter works in three steps. Nothing on disk changes until you press **Move** in step 3.

### Step 1: Choose photos

1. **Source folder:** click **Choose…** and pick the folder with the unsorted photos (for example the SD card's DCIM folder or an import folder). Subfolders are included unless you untick the box.
2. **Destination folder:** pick the folder where your sorted folders live (for example `Pictures/Photos`). Photo Sorter reads the folders already there, so it can add new photos to a matching folder instead of creating a duplicate.
3. **Grouping:** a new collection starts after a gap of **7 days**, or a location jump of **20 km** (only for photos with GPS).
   - "Keep trips together" (on by default) means a location jump only splits when you leave home or come back, so day trips during a holiday stay in one collection.
   - **Home location:** detected from where most of your phone photos are taken, over many days. If your first scan holds only a holiday, type your home as `latitude, longitude` (for example `48.14, 11.58`). Photo Sorter remembers it.
4. Click **Scan and group**. A summary shows the number of photos, collections, cameras and GPS photos.
5. **Camera clocks:** if a camera's clock was wrong (for example still on home time abroad), type a correction such as `+1:00` or `-0:03`. Groups update at once.
   - **Line up cameras…** shows one day's photos from all cameras in time order, including photos already sorted into the destination, so you can match the same moment.
   - Corrections are remembered per camera (model and serial number). They are only used for grouping; the dates inside your files are not changed.
6. Click **Review collections**.

### Step 2: Name, rate and tag

- **Collections** are listed on the left. Each one becomes a folder.
- **Folder name:** the `yyyymm-` part comes from the first photo; type the rest, for example `Sardinia` gives `202607-Sardinia`.
  - If the dates match a folder already in the destination, a blue box says so and the photos are **added to that folder**. You can also give them that folder's keywords, or choose **Use a different name**.
  - Typing a name that already exists in the destination also adds to that folder.
- **Holiday / Daily life:** the label is a guess from GPS; click it to change.
- **Merge with previous** joins a collection with the one above it. **Split at selection** starts a new collection at the first selected photo.
- **Filters:** day, format (RAW, JPEG, HEIC), camera, GPS, rating and deletion marks.
- **Selecting:**
  - click a photo to select it;
  - Shift-click to select a range;
  - ⌘-click to add or remove one;
  - **Select all shown** (or ⌘A) selects everything the filters show;
  - double-click opens a photo large (← → to browse, Esc to close).
- **Rating:** click a star or press **0–5**. Click the same star again to clear it.
- **Keywords:** type a keyword and press Enter. Separate several with commas. Click × to remove one. **Add these keywords to all** copies them to every photo in the collection.
- **Mark for deletion:** press **X** or use the button. Marked photos are moved to `_to_delete` in the destination; nothing is erased.

Your work is saved continuously. If you quit and start again, ratings, keywords and names are still there.

### Step 3: Move

1. Check the **destination folder**.
2. Read **What will happen** and the **Preview**:
   - each folder is marked **new** or **existing**, with how many photos are added;
   - files that get a number because the name is taken are listed, for example `IMG_0001_2.JPG`;
   - files already there (identical) are skipped and stay in the source folder.
3. Collections without a name are called `yyyymm-unnamed`. The warning has a link to name them first.
4. Click **Move N photos**, then **Confirm**. Keep the window open until it finishes.

What happens to each photo:

| Photo | Moved to | Keywords and rating written to |
|---|---|---|
| RAW (CR2, CR3, NEF, ARW, RAF, ORF, RW2 …) | the collection folder | a `.xmp` sidecar with the same name (`DSC0001.xmp`) |
| RAW + JPEG pair | both together | the sidecar and inside the JPEG |
| JPEG, DNG, PNG, TIFF | the collection folder | inside the file |
| HEIC | the collection folder | inside the file **and** a `.xmp` sidecar (changeable under "HEIC keywords" in step 3) |
| Marked for deletion | `_to_delete` | nothing |
| Videos and other files | stay where they are | nothing |

Rules: nothing is overwritten, an existing `.xmp` is updated rather than replaced (so Lightroom's edits in it are kept), and every step is logged.

### Undo

On the final screen, **Undo this move**, then **Confirm**, moves every file back to where it was and removes sidecars Photo Sorter created. Sidecars that already existed are restored from a copy. Keywords written *inside* JPEG/HEIC/DNG files stay in those files.

---

## 3. In Lightroom Classic

- **New photos:** File › Import, select the destination folder, choose **Add** (not Copy or Move), Import.
- **Folders already in the catalog:** right-click the folder in the Folders panel › **Synchronize Folder…** › Synchronize. Then select its photos › **Metadata › Read Metadata from Files**.
- **HEIC:** test once. Sort a few iPhone photos, import them, and check that keywords and stars show up. If they do not, tell me, and switch "HEIC keywords" in step 3.
- Empty `_to_delete` yourself in Finder when you have checked it.

---

## 4. Stopping, data and resetting

- **To stop:** click **Quit** in the browser, or close the Terminal window.
- **Saved data:** Photo Sorter keeps its own data in `~/.photosorter` (a hidden folder in your home folder): work in progress, camera corrections, preview cache and move logs (`journals`). It writes nothing into your photo folders except the photos, sidecars and folders you asked for.
- **To start completely fresh:** quit, then in Terminal run `rm -rf ~/.photosorter`.

---

## 5. Problems

| What you see | What to do |
|---|---|
| "ExifTool was not found" | Install ExifTool (1.2). If you installed it elsewhere: `PHOTOSORTER_EXIFTOOL=/path/to/exiftool python3 -m photosorter` |
| "Python 3.9 or newer was not found" | Install Python (1.1) |
| The browser does not open | Copy the link shown in the Terminal window into the browser. It only works while that window is open. |
| "Open the link from the Terminal window" | You opened the page without its key; use the link printed in Terminal |
| A preview is grey ("No preview") | Only the preview is missing (some new RAW formats); the photo itself is handled normally |
| A preview is sideways | Only the preview is affected; files are never rotated. Please tell me the camera and format |
| "Choose…" does nothing | Look for the folder dialog behind the browser window, or type the path |
| A move reports problems | Read the list on the final screen; the log path is shown there. Undo works even after problems. |

---

## 6. Updating

If you installed Photo Sorter with git (GitHub), `Start Photo Sorter.command` downloads the newest version each time it starts. That download contains only Photo Sorter's own code; nothing is sent. Without git, replace the `PhotoSorter` folder with the new one; your saved work in `~/.photosorter` is kept.
