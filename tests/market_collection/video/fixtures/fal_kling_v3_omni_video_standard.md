# Kling O3 Reference Video to Video [Standard]

> Kling O3 Omni generates new shots guided by an input reference video, preserving cinematic language such as motion, and camera style to produce seamless scene continuity.


## Overview

- **Endpoint**: `https://fal.run/fal-ai/kling-video/o3/standard/video-to-video/reference`
- **Model ID**: `fal-ai/kling-video/o3/standard/video-to-video/reference`
- **Category**: video-to-video
- **Kind**: inference
**Tags**: video-to-video



## Pricing

For every second of video you generated, you will be charged **$0.126**. For example, a 5s video will cost **$0.63**.

For more details, see [fal.ai pricing](https://fal.ai/pricing).

## API Information

This model can be used via our HTTP API or more conveniently via our client libraries.
See the input and output schema below, as well as the usage examples.


### Input Schema

The API accepts the following input parameters:


- **`enable_safety_checker`** (`boolean`, _optional_):
  Disabling safety checks requires account authorization; unauthorized requests are always checked. Default value: `true`
  - Default: `true`

- **`prompt`** (`string`, _required_):
  Text prompt for video generation. Reference video as @Video1.
  - Examples: "Replace both character with @Element1 and @Element2"

- **`video_url`** (`string`, _required_):
  Reference video URL. Only .mp4/.mov formats, 3-15s duration, 720-3840px resolution, max 200MB.
  - Examples: "https://storage.googleapis.com/falserverless/example_inputs/kling-o3/standard-v2v-reference/video_reference.mp4"

- **`image_urls`** (`list<string>`, _optional_):
  Reference images for style/appearance. Reference in prompt as @Image1, @Image2, etc. Maximum 4 total (elements + reference images) when using video.
  - Array of string
  - Examples: null

- **`keep_audio`** (`boolean`, _optional_):
  Whether to keep the original audio from the reference video. Default value: `true`
  - Default: `true`
  - Examples: false

- **`elements`** (`list<KlingV3ImageElementInput>`, _optional_):
  Elements (characters/objects) to include. Reference in prompt as @Element1, @Element2.
  - Array of KlingV3ImageElementInput
  - Examples: [{"reference_image_urls":["https://storage.googleapis.com/falserverless/example_inputs/kling-o3/standard-v2v-reference/element1_reference1.png"],"frontal_image_url":"https://storage.googleapis.com/falserverless/example_inputs/kling-o3/standard-v2v-reference/element1_front.png"},{"reference_image_urls":["https://storage.googleapis.com/falserverless/example_inputs/kling-o3/standard-v2v-reference/element2_reference1.png"],"frontal_image_url":"https://storage.googleapis.com/falserverless/example_inputs/kling-o3/standard-v2v-reference/element2_front.png"}]

- **`shot_type`** (`string`, _optional_):
  The type of multi-shot video generation. Default value: `"customize"`
  - Default: `"customize"`

- **`aspect_ratio`** (`AspectRatioEnum`, _optional_):
  Aspect ratio. Default value: `"auto"`
  - Default: `"auto"`
  - Options: `"auto"`, `"16:9"`, `"9:16"`, `"1:1"`
  - Examples: "auto"

- **`duration`** (`Enum`, _optional_):
  Video duration in seconds (3-15s for reference video).
  - Options: `"3"`, `"4"`, `"5"`, `"6"`, `"7"`, `"8"`, `"9"`, `"10"`, `"11"`, `"12"`, `"13"`, `"14"`, `"15"`
  - Examples: "5"



**Required Parameters Example**:

```json
{
  "prompt": "Replace both character with @Element1 and @Element2",
  "video_url": "https://storage.googleapis.com/falserverless/example_inputs/kling-o3/standard-v2v-reference/video_reference.mp4"
}
```

**Full Example**:

```json
{
  "enable_safety_checker": true,
  "prompt": "Replace both character with @Element1 and @Element2",
  "video_url": "https://storage.googleapis.com/falserverless/example_inputs/kling-o3/standard-v2v-reference/video_reference.mp4",
  "image_urls": null,
  "keep_audio": false,
  "elements": [
    {
      "reference_image_urls": [
        "https://storage.googleapis.com/falserverless/example_inputs/kling-o3/standard-v2v-reference/element1_reference1.png"
      ],
      "frontal_image_url": "https://storage.googleapis.com/falserverless/example_inputs/kling-o3/standard-v2v-reference/element1_front.png"
    },
    {
      "reference_image_urls": [
        "https://storage.googleapis.com/falserverless/example_inputs/kling-o3/standard-v2v-reference/element2_reference1.png"
      ],
      "frontal_image_url": "https://storage.googleapis.com/falserverless/example_inputs/kling-o3/standard-v2v-reference/element2_front.png"
    }
  ],
  "shot_type": "customize",
  "aspect_ratio": "auto",
  "duration": "5"
}
```


