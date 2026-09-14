# EP28 reference-interval example

Use the included CC0 Kenya example, keeping source-specific assay units and selection context.

```sh
tametools analyze web/backend/examples/kenya_reference.tame RI_EP28 --option METHOD=NONPARAMETRIC --option CI_METHOD=NONE --option OUTLIER_METHOD=NONE --option OUTPUT_DIR=outputs/kenya_ri
```

The output includes result tables, processing history, and a report. Use a new output directory. These public-data candidates demonstrate the software and do not establish a reference interval for another laboratory.

For methods, bootstrap confidence intervals, partitions, LAVE, verification, and required population declarations, see the [plugin guide](../../docs/REFERENCE_INTERVAL_EP28_GUIDE_KO.md). Source attribution is in [the example README](../../web/backend/examples/README_KO.md).
