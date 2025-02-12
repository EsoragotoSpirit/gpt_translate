This fork of `gpt_translate` includes the config files (mainly glossary) for translating the [StarRocks docs](https://docs.starrocks.io) from English into Chinese.

Please submit a PR to the parent of this fork at [`tcapelle/gpt_translate`](https://github.com/tcapelle/gpt_translate) if you change any files other than:
- this README
- `config/**`

Use at StarRocks:

0. Clone

  There are differences between our use at StarRocks and upstream. Clone this repo and install it locally:

  - Clone this repo and change dir into it
  - Build the Docker image
  ```bash
  docker build -f translation.Dockerfile -t translate .
  ```

## STOP HERE

Switch back to the [`starrocks/docs/translation/README`](https://github.com/StarRocks/starrocks/blob/main/docs/translation/README.md) and set up the environment.
