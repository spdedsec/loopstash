# YouTube Publication Scheduling Engine

**LOOPSTASH** provides timezone-aware calendar planning and automated private uploads with YouTube's `status.publishAt` feature.

---

## 📅 Scheduling Architecture

YouTube Data API v3 natively supports scheduled publishing:
1. Video is uploaded with `status.privacyStatus = "private"`.
2. Video is assigned a future timestamp in `status.publishAt` formatted in UTC ISO8601 (`YYYY-MM-DDTHH:MM:SSZ`).
3. YouTube keeps the video private until that exact moment, then automatically transitions it to public.

---

## 🛠️ Scheduling Commands

### Previewing Publication Slots

Preview your publication calendar before scheduling or uploading any video files:

```bash
loopstash schedule preview \
  --start-date 2026-12-01 \
  --start-time 14:00 \
  --timezone "Asia/Kolkata" \
  --count 7 \
  --interval daily
```

Supported intervals:
- `daily` / `1d` / `1`
- `every_2d` / `2d` / `2`
- `every_3d` / `3d`
- `weekly` / `7d`
- Custom hours or days: `12h`, `6h`, `4d`

### Creating Scheduled Releases

Schedule an individual video or a batch of videos across generated slots:

```bash
# Schedule single media item
loopstash schedule create med_01 \
  --channel UC_MY_CHANNEL \
  --start-date 2026-12-01 \
  --start-time 18:00 \
  --timezone "America/New_York" \
  --confirm-rights

# Schedule batch of media items with 2-day intervals
loopstash schedule create med_01 med_02 med_03 \
  --channel UC_MY_CHANNEL \
  --start-date 2026-12-01 \
  --start-time 18:00 \
  --interval every_2d \
  --timezone "America/New_York" \
  --confirm-rights
```

### Inspecting Active Schedules
```bash
loopstash schedule list
```

### Rescheduling a Video
Modify the future release date and time. If the video is already uploaded to YouTube, LOOPSTASH communicates with YouTube Data API v3 to update `status.publishAt` remotely:

```bash
loopstash schedule reschedule <JOB_OR_MEDIA_ID> \
  --date 2026-12-10 \
  --time 19:30 \
  --timezone "Asia/Kolkata"
```

### Cancelling a Schedule
Cancel a scheduled release in the local database:

```bash
loopstash schedule cancel <JOB_OR_MEDIA_ID>
```
