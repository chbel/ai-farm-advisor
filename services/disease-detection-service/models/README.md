# Disease models

Trained models are **not stored in git**. Layout (one folder per crop):

```
models/
  tomato/
    model.onnx       # exported by ml/disease-detection/train_tomato.py
    metadata.json    # labels, preprocessing, temperature, metrics, dataset licenses
```

To install the tomato model: run `ml/disease-detection/train_tomato_colab.ipynb` in
Google Colab, download `tomato_disease_model.zip`, and extract it into `models/tomato/`.
Docker Compose mounts this folder read-only into the service.
