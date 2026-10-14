# Slearn – Instagram Reels (English)

Six promo reels for Slearn, each 20–35 seconds, vertical 9:16 (1080 × 1920).

Every reel has:

- a **shot list** (what is on screen, second by second),
- **on-screen text** (keep it short, readable without sound),
- a **voiceover** – the same text sits ready to paste into a text-to-speech tool under `voiceover/`,
- a **caption** with hashtags.

The voiceover files contain only the spoken words – no stage directions, no emojis, no brackets – so a TTS tool reads them as written. Line breaks mark a short pause.

## Ground rules (so the ads stay honest)

Every claim below comes from what Slearn actually does today. Please keep it that way when you edit:

- **No prices.** They are announced at launch; the CTA is the waitlist.
- **No system requirements beyond what is real:** Windows (x64) and macOS (Apple Silicon and Intel). Personal training needs an NVIDIA GPU with 6 GB+ or an Apple Silicon Mac with 16 GB+.
- **Not "AI does your homework".** The assistant answers from *your* documents and says when it finds nothing.
- **WebUntis is optional.** Don't imply a school needs it.
- The landing page is currently **German only** – if the reels run for an English audience, mention that or link straight to the waitlist form.

## Tips for recording

- **Screen recordings:** switch Slearn to English (*Settings → Language*), use demo data, no real names or grades.
- **Look:** Slearn is black and white with thin lines. Keep overlays the same: white or black text, one font (Geist or a clean sans), no gradients, no sparkle emojis.
- **Sloth:** Slearn sloth (`frontend/components/assistant/faultier.tsx`) can be used as a sticker or animated reaction – idle, listening, thinking, speaking, happy, sleeping.
- **Voice:** a calm, friendly, slightly dry voice fits best (think "older sibling who has it together", not "hype announcer"). Speed 1.0–1.1×.
- **Pronunciation hints for TTS:** "Slearn" = *LERN-rowm* (rhymes with "brown"); "WebUntis" = *web-OON-tis*. If your tool mispronounces them, write them phonetically in the voiceover file only, e.g. `Lern-rowm`.
- **Subtitles:** always on – most people watch muted.

---

## Reel 1 – "Where was that homework again?"

**Goal:** pain point → calm overview. **Length:** ~25 s. **Music:** lo-fi, starts muffled, gets clear at 0:08.

| Time | On screen | On-screen text |
| --- | --- | --- |
| 0:00–0:03 | Close-up: messy desk, sticky notes, three apps open on a phone, school planner with crossed-out dates. | **Homework in 4 places?** |
| 0:03–0:08 | Fast cuts: WhatsApp group, photo of the board, planner, notes app. | *(none)* |
| 0:08–0:14 | Hard cut to Slearn dashboard, clean. Slow zoom on "today". | **One question: what's due today?** |
| 0:14–0:19 | Timetable with a cancelled lesson highlighted. | **Timetable from WebUntis – cancellations included** |
| 0:19–0:23 | Homework + exams + events list scrolling. | **Homework. Exams. Events. One place.** |
| 0:23–0:25 | Black screen, sloth waves, logo. | **Slearn – join the waitlist** |

**Voiceover** (`voiceover/01-homework.txt`):

> Homework on a sticky note. Exam date in the group chat. Timetable in another app.
> Slearn puts it all in one place.
> Open it, and it answers one question: what's due today?
> It can pull your timetable from WebUntis, so cancelled lessons show up right away.
> Slearn. Join the waitlist.

**Caption:**
> Your planner, your notes app and three group chats walk into a bar… 🦥
> Slearn keeps homework, exams and your timetable in one place. Waitlist link in bio.
> #studytok #studentlife #studyapp #homework #schoollife #studyorganization #productivity #lernraum

---

## Reel 2 – "Exam in 10 days. Now what?"

**Goal:** show the exam study-plan generator. **Length:** ~30 s. **Music:** steady beat, slight tension.

| Time | On screen | On-screen text |
| --- | --- | --- |
| 0:00–0:03 | Phone calendar: "Maths exam" in 10 days. Face-palm or pen drop. | **Exam in 10 days.** |
| 0:03–0:07 | Blank page, pen hovering. | **Where do you even start?** |
| 0:07–0:13 | Screen recording: create exam, add topics (e.g. derivatives, integrals, curve sketching). | **Add your exam + topics** |
| 0:13–0:20 | Topics with confidence levels "unsure" … "solid". Click "generate plan". | **Weakest topic goes first** |
| 0:20–0:26 | Generated tasks spread across the days, chained in order. | **Spread out until exam day** |
| 0:26–0:30 | Sloth with thumbs up, logo. | **Slearn – waitlist in bio** |

**Voiceover** (`voiceover/02-exam-plan.txt`):

> Maths exam in ten days. Where do you even start?
> In Slearn, you add the exam and its topics.
> Then you tell it how sure you are about each one.
> It builds a study plan, spread out until exam day.
> And your weakest topic comes first, because it needs the most time.
> Click again, and nothing gets added twice.
> Slearn. Join the waitlist.

**Caption:**
> Stop cramming the night before. Slearn turns an exam and its topics into a study plan – weakest topic first. 📚
> #exam #examprep #studyplan #studytips #studygram #studentlife #lernraum

---

## Reel 3 – "An assistant that actually knows your notes"

**Goal:** the assistant answers from your own documents. **Length:** ~32 s. **Music:** soft, curious.

| Time | On screen | On-screen text |
| --- | --- | --- |
| 0:00–0:03 | Hand takes a photo of handwritten class notes. | **Snap your notes** |
| 0:03–0:08 | Upload in Slearn; document shows subject, topic and date detected. | **It reads them – subject, topic, date** |
| 0:08–0:15 | Assistant chat: "What did we write about derivatives?" → answer quoting the notes. | **Ask about your own notes** |
| 0:15–0:21 | "Quiz me on this" → question → answer → feedback. | **Let it quiz you** |
| 0:21–0:27 | Question about something not in the documents → honest "nothing found". | **No notes? It says so.** |
| 0:27–0:32 | Sloth "thinking" → "happy". Logo. | **Slearn – waitlist in bio** |

**Voiceover** (`voiceover/03-assistant.txt`):

> Take a photo of your notes.
> Slearn reads them, and recognises the subject, the topic and the date.
> Now ask: what did we write about derivatives?
> The assistant answers from your notes. Not from somewhere on the internet.
> Want to practise? Let it quiz you.
> And if your notes don't cover something, it tells you, instead of making it up.
> Slearn. Join the waitlist.

**Caption:**
> An AI that answers from YOUR notes – and admits when it doesn't know. 🦥📝
> #studywithme #studyhacks #notes #ai #studentlife #studyapp #lernraum

---

## Reel 4 – "Your grades are nobody's business"

**Goal:** privacy and local-first. **Length:** ~28 s. **Music:** minimal, confident.

| Time | On screen | On-screen text |
| --- | --- | --- |
| 0:00–0:03 | Text only, black on white, typed out letter by letter. | **Who else can see your grades?** |
| 0:03–0:09 | Laptop with Slearn open; Wi-Fi switched off in the menu bar. | **Runs on your computer** |
| 0:09–0:14 | Assistant answering offline, small "local" badge visible. | **Assistant works offline** |
| 0:14–0:20 | Settings with toggles (share grades, write calendar, learn from conversations) – all off. | **Everything optional is off by default** |
| 0:20–0:25 | Three lines appear: No account. No cloud. No tracking. | **No account. No cloud. No tracking.** |
| 0:25–0:28 | Sleeping sloth, logo. | **Slearn – waitlist in bio** |

**Voiceover** (`voiceover/04-privacy.txt`):

> Your grades, your timetable, your notes. That's personal.
> Slearn desktop app keeps everything on your own computer.
> No account. No cloud. No tracking.
> The assistant, speech recognition and read-aloud run locally, and work offline.
> Sharing grades, writing to your calendar, learning from your conversations: all of it is off until you turn it on.
> Slearn. Join the waitlist.

**Caption:**
> Built for student data: the desktop app keeps everything on your device. Online AI? Optional. 🔒
> #privacy #dataprivacy #studentlife #studyapp #localfirst #lernraum

---

## Reel 5 – "Meet the sloth"

**Goal:** character / fun, show voice conversation mode. **Length:** ~25 s. **Music:** playful, slow.

| Time | On screen | On-screen text |
| --- | --- | --- |
| 0:00–0:03 | Sloth sleeping, slowly opens one eye. | **Slow? Yes. Lazy? No.** |
| 0:03–0:09 | Click "Start conversation"; sloth switches to listening. Student speaks. | **Just talk to it** |
| 0:09–0:15 | Student: "I have a history test next Friday." Suggestion card appears → click "Add". | **It suggests – you decide** |
| 0:15–0:20 | Sloth "happy" pose, entry visible in exams list. | **Nothing is added without you** |
| 0:20–0:25 | Sloth eyes follow the cursor. Logo. | **Slearn – waitlist in bio** |

**Voiceover** (`voiceover/05-sloth.txt`):

> This is Slearn sloth. Slow, but never lazy.
> Start a conversation, and just talk.
> I have a history test next Friday.
> The sloth suggests an entry. You check it, and tap add.
> Nothing goes into your planner without you.
> Slearn. Join the waitlist.

> Tip: record the line "I have a history test next Friday." with a second voice (the student), the rest with the narrator voice.

**Caption:**
> Your new study buddy is a sloth. It listens, suggests, and never adds anything without asking. 🦥🎙️
> #sloth #studybuddy #studyapp #studentlife #voiceassistant #lernraum

---

## Reel 6 – "What do I need on the next test?"

**Goal:** grades with weighted average and projection. **Length:** ~25 s. **Music:** upbeat, light.

| Time | On screen | On-screen text |
| --- | --- | --- |
| 0:00–0:03 | Student doing maths on a napkin, confused. | **What do I need on the next test?** |
| 0:03–0:09 | Slearn grades: individual grades with weights. | **Every grade, weighted** |
| 0:09–0:15 | Average per subject + trend line. | **Average + trend per subject** |
| 0:15–0:21 | Projection: "what has to come next so it's enough". | **The projection does the maths** |
| 0:21–0:25 | Napkin crumpled into the bin. Logo. | **Slearn – waitlist in bio** |

**Voiceover** (`voiceover/06-grades.txt`):

> What do I need on the next test to pass?
> Stop calculating on a napkin.
> Slearn keeps every grade with its weighting, and shows your average and trend for each subject.
> The projection tells you what still has to come, so it's enough.
> Slearn. Join the waitlist.

**Caption:**
> Weighted averages, trends and "what do I still need?" – without the napkin maths. 📈
> #grades #studentlife #school #studyapp #studytips #lernraum

---

## Posting plan (suggestion)

| Week | Reel | Why this order |
| --- | --- | --- |
| 1 | Reel 1 – Homework | Broadest pain point, best for reach |
| 1 | Reel 5 – Sloth | Character people remember |
| 2 | Reel 2 – Exam plan | Strong around exam season |
| 2 | Reel 3 – Assistant | Shows the "wow" feature |
| 3 | Reel 6 – Grades | Very shareable question |
| 3 | Reel 4 – Privacy | Builds trust, good for parents |
