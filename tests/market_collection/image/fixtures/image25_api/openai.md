# Image generation

> For the complete documentation index, see [llms.txt](/llms.txt). Markdown versions of documentation pages are available by appending `.md` to the page URL.

## Overview

The API lets you generate and edit images from text prompts using `gpt-image-2.5-sunburst` and `gpt-image-2.5-flare`. Choose Sunburst for workflows where editing precision matters most, and Flare for fast, high-quality everyday image generation. You can access image generation capabilities through two APIs:

### Image API

The [Image API](https://developers.openai.com/api/reference/resources/images) provides two endpoints, each with distinct capabilities:

- **Generations**: [Generate images](#generate-images) from scratch based on a text prompt
- **Edits**: [Modify existing images](#edit-images) using a new prompt, either partially or entirely

### Responses API

The [Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create#responses-create-tools) allows you to generate images as part of conversations or multi-step flows. It supports image generation as a [built-in tool](https://developers.openai.com/api/docs/guides/tools?api-mode=responses), and accepts image inputs and outputs within context.

Compared to the Image API, it adds:

- **Multi-turn editing**: Iteratively make high fidelity edits to images with prompting
- **Flexible inputs**: Accept image [File](https://developers.openai.com/api/reference/resources/files) IDs as input images, not just bytes

For mainline models that can call the image generation tool, refer to [supported models](#supported-models).

### Choosing the right API

- If you only need to generate or edit a single image from one prompt, the Image API is your best choice.
- If you want to build conversational, editable image experiences with GPT Image, go with the Responses API.

With the Image API, set `model` to `gpt-image-2.5-sunburst` or `gpt-image-2.5-flare` directly. With the Responses API, select a supported mainline model at the top level and specify `gpt-image-2.5-sunburst` or `gpt-image-2.5-flare` in the image generation tool's `model` field.

Both APIs let you [customize output](#customize-image-output) by adjusting quality, size, format, and compression.

To ensure these models are used responsibly, you may need to complete the [API
  Organization
  Verification](https://help.openai.com/en/articles/10910291-api-organization-verification)
  from your [developer
  console](https://platform.openai.com/settings/organization/general) before
  using GPT Image models.

<div
  className="not-prose"
  style={{ float: "right", margin: "10px 0 10px 10px" }}
>
  <img src="https://developers.openai.com/images/image-25-article/mug.png"
    alt="A beige coffee mug on a wooden table"
    style={{ height: "180px", width: "auto", borderRadius: "8px" }}
  />



## Generate Images

You can use the [image generation endpoint](https://developers.openai.com/api/reference/resources/images) to create images based on text prompts, or the [image generation tool](https://developers.openai.com/api/docs/guides/tools?api-mode=responses) in the Responses API to generate images as part of a conversation.

To learn more about customizing the output (size, quality, format, compression), refer to the [customize image output](#customize-image-output) section below.

You can set the `n` parameter to generate multiple images at once in a single request (by default, the API returns a single image).



Image API

    Generate an image


## Customize Image Output

You can configure the following output options:

- **Size**: Image dimensions (for example, `1024x1024`, `1024x1536`)
- **Quality**: Rendering quality (for example, `low`, `medium`, `high`)
- **Format**: File output format
- **Compression**: Compression level (0-100%) for JPEG and WebP formats
- **Background**: Transparent, opaque, or automatic

`size`, `quality`, and `background` support the `auto` option, where the model will automatically select the best option based on the prompt.

### Size and quality options

`gpt-image-2.5-sunburst` and `gpt-image-2.5-flare` add `xhigh` and `max` quality settings. Both default to `auto`. Earlier GPT Image models support quality settings up to `high`.

| Setting           | Options                                                               |
| ----------------- | --------------------------------------------------------------------- |
| Recommended sizes | `1024x1024` (square), `1536x1024` (landscape), `1024x1536` (portrait) |
| Quality           | `low`, `medium`, `high`, `xhigh`, `max`, `auto`                       |

Both models also support custom dimensions as `WIDTHxHEIGHT` strings, such as `1536x864`. Width and height must be multiples of 16, the aspect ratio must be between 1:3 and 3:1, and neither edge may exceed 3840 pixels. The total pixel count must be between 655,360 and 8,294,400 (4K). Resolutions above `2560x1440` are experimental.

For transparent backgrounds with either model, set `background: "transparent"` and use `output_format: "png"` or `"webp"`.

Use `quality: "low"` for quick drafts. For final assets, compare higher quality settings to find the right balance of detail, latency, and cost.


### GPT Image 2.5 costs

Responses API requests include the mainline model's token usage in addition to image generation costs.

Both GPT Image 2.5 models use the same token rates: $8 per million image input tokens, $2 per million cached image input tokens, $30 per million image output tokens, $5 per million text input tokens, and $1.25 per million cached text input tokens. See [pricing](https://developers.openai.com/api/docs/pricing#image-generation).

Use the response's `usage` to measure token consumption for your prompts, sizes, and quality settings. Equal token rates don't mean equal cost per image: token consumption can differ by model and quality setting. For older-model pricing examples, see [Earlier GPT Image models](#earlier-gpt-image-models).

### Cached input pricing

For GPT Image 2 and GPT Image 2.5, cached input pricing applies only to the image generation tool in the Responses API. It doesn't apply to direct Images API requests, including `/v1/images/edits`.

Cached image generation inputs are reflected in billing, but their cached token counts aren't included in the Responses API output. The response's `usage` can't verify these cache hits.




