# Persona

Everything the model is told about who Sanya is and how she behaves lives in this folder. None of
it is written in code.

| File | Holds | Sent to the model |
|---|---|---|
| `SOUL.md` | Who she is | Yes, first |
| `VOICE.md` | How she talks | Yes |
| `POLICY.md` | How she handles memory, facts, pictures and language | Yes |
| `RULES.md` | The layout of the conversation and the four rule areas | Yes, last |
| `lines.yaml` | Her fixed lines: model failure, cut-off, refusal, brush-off, error, image failed. Each may have several wordings; one is picked at random. | No. The bot posts them itself. |
| `status.yaml` | The line under her name, by time of day | No. The bot sets it. |
| `SECURITY_CHECK.md` | The instruction for the security check, which screens each message addressed to her | Only to the check, never to her |
| `IMAGE_DESCRIPTION.md` | The instruction for describing an overheard picture | Only to the describer, never to her |
| `MEMORY_MISSION.md` | The instruction the memory service follows when it turns chat into facts | Only to the memory service, when the setup wizard puts it on her bank |

These files were drafted on 4 October 2026 from the owner's interview, reworded through the
behaviour test rounds, and settled with the owner the same day in a second interview on every
rule that was the builder's wording. Both are recorded in `docs/SETTINGS.md` under "Her
character". Edit the files directly; she reads them when she starts.
