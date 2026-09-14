# Clinical chemistry workflow examples

The included synthetic `clinical_chem.tame` exercises typed result columns, age and sex grouping, comparator handling, and chained processing.

```sh
tametools validate tutorial/14_capability_probe/clinical_chem.tame
tametools describe tutorial/14_capability_probe/clinical_chem.tame
python tutorial/14_capability_probe/examples/run_all.py
```

Inspect each example's META and write new outputs to a separate directory. The [EP28 guide](../../docs/REFERENCE_INTERVAL_EP28_GUIDE_KO.md) describes the current reference-interval plugin.
