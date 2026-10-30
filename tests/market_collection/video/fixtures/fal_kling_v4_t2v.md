# Kling 4.0 Text to Video

> Kling 4.0 generates videos from text prompts at 720p or 1080p, with durations from 3 to 30 seconds and support for per-shot prompts.  


## Overview

- **Endpoint**: `https://fal.run/kling/v4/text-to-video`
- **Model ID**: `kling/v4/text-to-video`
- **Category**: text-to-video
- **Kind**: inference
**Tags**: text-to-video, multi-shot, cinematic, kling



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

- **`aspect_ratio`** (`AspectRatioEnum`, _optional_):
  Aspect ratio of the generated video. Default value: `"16:9"`
  - Default: `"16:9"`
  - Options: `"16:9"`, `"9:16"`, `"1:1"`, `"21:9"`



**Required Parameters Example**:

```json
{}
```

**Full Example**:

```json
{
  "prompt": "A detective in a black trench coat stands on a rooftop in the rain, looking through a telescope at the street below.",
  "multi_prompt": null,
  "duration": 5,
  "resolution": "1080p",
  "aspect_ratio": "16:9"
}
```


