# Kling 4.0 Reference to Video

> Kling 4.0 generates videos from text prompts and image or video references at 720p or 1080p, with durations from 3 to 30 seconds and per-shot prompts.  


## Overview

- **Endpoint**: `https://fal.run/kling/v4/reference-to-video`
- **Model ID**: `kling/v4/reference-to-video`
- **Category**: video-to-video
- **Kind**: inference
**Tags**: reference-to-video, multi-shot, image-reference, video-reference, kling



## Pricing

Video costs **$0.157** per second at **720p** and **$0.235** per second at **1080p**. With video references or video elements, it costs **$0.094** per second at **720p** and **$0.141** per second at **1080p**, billed on the input plus output length (minimum 2× the output).

For more details, see [fal.ai pricing](https://fal.ai/pricing).

## API Information

This model can be used via our HTTP API or more conveniently via our client libraries.
See the input and output schema below, as well as the usage examples.


### Input Schema

The API accepts the following input parameters:


- **`prompt`** (`string`, _optional_):
  Text prompt. Reference inputs as @Image1, @Video1, @Element1, etc. Either prompt or multi_prompt must be provided.
  - Examples: "@Element1 and @Element2 are having dinner at the table in @Image1"

- **`multi_prompt`** (`list<KlingV4Shot>`, _optional_):
  Per-shot prompts for multi-shot generation. The video duration is the sum of the shot durations.
  - Array of KlingV4Shot
  - Examples: null

- **`duration`** (`integer`, _optional_):
  Video duration in seconds (3-30). Default value: `5`
  - Default: `5`
  - Range: `3` to `30`

- **`start_image_url`** (`string`, _optional_):
  Optional image to use as the first frame. Counts toward the image reference limit.

- **`middle_image_urls`** (`list<string>`, _optional_):
  Optional ordered keyframes between the first and last frames (max 8). Count toward the image reference limit.
  - Array of string
  - Examples: null

- **`end_image_url`** (`string`, _optional_):
  Optional image to use as the last frame. Counts toward the image reference limit.

- **`image_urls`** (`list<string>`, _optional_):
  Reference images, referenced as @Image1, @Image2, ... (max 10).
  - Array of string
  - Examples: null

- **`video_urls`** (`list<string>`, _optional_):
  Reference videos (3-30s each, 30s combined with element videos), referenced as @Video1, @Video2, ... (max 5).
  - Array of string
  - Examples: null

- **`elements`** (`list<KlingV4Element>`, _optional_):
  Characters/objects, referenced as @Element1, @Element2, ... (max 7). Up to 3 elements may carry a voice_id and up to 3 may be video elements.
  - Array of KlingV4Element
  - Examples: null

- **`voice_ids`** (`list<string>`, _optional_):
  Voices referenced in the prompt as @Voice1, @Voice2, ... (max 3, including voices bound to elements). Create one with https://fal.ai/models/fal-ai/kling-video/create-voice
  - Array of string
  - Examples: null

- **`resolution`** (`ResolutionEnum`, _optional_):
  Output resolution. Default value: `"1080p"`
  - Default: `"1080p"`
  - Options: `"720p"`, `"1080p"`

- **`aspect_ratio`** (`AspectRatioEnum`, _optional_):
  Aspect ratio of the generated video. 'auto' adapts it to the references. Default value: `"16:9"`
  - Default: `"16:9"`
  - Options: `"auto"`, `"16:9"`, `"9:16"`, `"1:1"`, `"21:9"`



**Required Parameters Example**:

```json
{}
```

**Full Example**:

```json
{
  "prompt": "@Element1 and @Element2 are having dinner at the table in @Image1",
  "multi_prompt": null,
  "duration": 5,
  "middle_image_urls": null,
  "image_urls": null,
  "video_urls": null,
  "elements": null,
  "voice_ids": null,
  "resolution": "1080p",
  "aspect_ratio": "16:9"
}
```


