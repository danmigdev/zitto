# Notice

Required Notice: Copyright 2026 danmigdev (https://github.com/danmigdev/zitto)

zitto is licensed under the PolyForm Noncommercial License 1.0.0: see [LICENSE.md](LICENSE.md).

This repository contains only the code and the documents of zitto. It does not contain, and
does not distribute, the models and the libraries below: they are downloaded from their own
sources to the computer of whoever installs zitto, each under its own terms.

## Models

- **BS-Roformer-SW**. Author, origin and licence are unknown. The
  [copy on Hugging Face](https://huggingface.co/enerjazzer/BS-ROFO-SW-Fixed) says "License:
  unknown", and according to [another one](https://huggingface.co/elicwhite/bs-roformer-sw-6stem-onnx)
  who first re-uploaded it was not involved in training it and does not know where it comes
  from
- **Mel-Roformer Karaoke**, by becruily. [Its page](https://huggingface.co/becruily/mel-band-roformer-karaoke)
  states no licence
- **MDX23C DrumSep**, by aufr33 and jarredou. Their page is no longer there. According to
  [a package that uses it](https://pypi.org/project/mdxnet-infer/), no licence came with it
- **Ultimate Vocal Remover** (17_HP-Wind_Inst; with `stated` also UVR-MDX-NET Main and
  6_HP-Karaoke), by Anjok07 and aufr33 and the other developers of UVR. Their README asks: "For all third-party application
  developers who wish to use our models, please honor the MIT license by providing credit to
  UVR and its developers." https://github.com/Anjok07/ultimatevocalremovergui
- **Open-Unmix UMX-L** (with `stated`), by Fabian-Robert Stöter, Stefan Uhlich, Antoine Liutkus
  and Yuki Mitsufuji. Creative Commons Attribution Non Commercial Share Alike 4.0 International: not
  for commercial use. https://zenodo.org/records/5069601

Whoever holds rights on a model and does not want it to be used here can open an issue in this
repository: zitto will stop using it. To use only models whose authors state their terms, set
the environment variable `ZITTO_MODELS` to `stated`.

## Libraries

Most of the libraries that setup.bat and setup.sh install are under permissive licences (MIT, BSD, ISC,
Apache 2.0). Those with other conditions:

| Library | Licence | Needed by |
| --- | --- | --- |
| diffq-fixed | CC BY-NC 4.0: not for commercial use | audio-separator |
| soxr | LGPL 2.1 or later | librosa |
| certifi, tqdm | MPL 2.0 | requests, audio-separator |

The versions of PyTorch for NVIDIA GPUs include CUDA libraries, under the terms of NVIDIA.
ffmpeg is a separate program, installed by the user and run as such; its licence (LGPL or GPL)
depends on the build.

Thanks to [audio-separator](https://github.com/nomadkaraoke/python-audio-separator) (MIT),
which runs most of the models, and to
[Open-Unmix](https://github.com/sigsep/open-unmix-pytorch) (MIT).
