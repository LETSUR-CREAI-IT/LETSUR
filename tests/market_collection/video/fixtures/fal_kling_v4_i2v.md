# Kling 4.0 Image to Video

> Kling 4.0 animates a starting image into a 3 to 30-second video at 720p or 1080p, with optional ending frames and per-shot prompts.  


## Overview

- **Endpoint**: `https://fal.run/kling/v4/image-to-video`
- **Model ID**: `kling/v4/image-to-video`
- **Category**: image-to-video
- **Kind**: inference
**Tags**: image-to-video, multi-shot, cinematic, kling



## Pricing

Video costs **$0.157** per second at **720p** and **$0.235** per second at **1080p**.

For more details, see [fal.ai pricing](https://fal.ai/pricing).

## API Information

This model can be used via our HTTP API or more conveniently via our client libraries.
See the input and output schema below, as well as the usage examples.


### Input Schema

The API accepts the following input parameters:


- **`prompt`** (`string`, _optional_):
  Text prompt. Either prompt or multi_prompt must be provided.
  - Examples: "A detective in a black trench coat stands on a rooftop in the rain, looking through a telescope at the street below."

- **`start_image_url`** (`string`, _required_):
  Image to use as the first frame of the video.
  
  Max file size: 30.0MB, Min width: 300px, Min height: 300px, Min aspect ratio: 0.40, Max aspect ratio: 2.50, Timeout: 20.0s
  - Examples: "https://v3b.fal.media/files/b/rabbit/NaslJIC7F2WodS6DFZRRJ.png"

- **`middle_image_urls`** (`list<string>`, _optional_):
  Ordered keyframes between the first and last frames (max 8).
  - Array of string
  - Examples: null

- **`end_image_url`** (`string`, _optional_):
  Image to use as the last frame of the video.
  - Examples: "https://v3b.fal.media/files/b/tiger/BwHi22qoQnqaTNMMhe533.png"

- **`multi_prompt`** (`list<KlingV4Shot>`, _optional_):
  Per-shot prompts for multi-shot generation. The video duration is the sum of the shot durations.
  - Array of KlingV4Shot
  - Examples: null

- **`duration`** (`integer`, _optional_):
  Video duration in seconds (3-30). Default value: `5`
  - Default: `5`
  - Range: `3` to `30`

- **`resolution`** (`ResolutionEnum`, _optional_):
  Output resolution. Default value: `"1080p"`
  - Default: `"1080p"`
  - Options: `"720p"`, `"1080p"`



**Required Parameters Example**:

```json
{
  "start_image_url": "https://v3b.fal.media/files/b/rabbit/NaslJIC7F2WodS6DFZRRJ.png"
}
```

**Full Example**:

```json
{
  "prompt": "A detective in a black trench coat stands on a rooftop in the rain, looking through a telescope at the street below.",
  "start_image_url": "https://v3b.fal.media/files/b/rabbit/NaslJIC7F2WodS6DFZRRJ.png",
  "middle_image_urls": null,
  "end_image_url": "https://v3b.fal.media/files/b/tiger/BwHi22qoQnqaTNMMhe533.png",
  "multi_prompt": null,
  "duration": 5,
  "resolution": "1080p"
}
```


