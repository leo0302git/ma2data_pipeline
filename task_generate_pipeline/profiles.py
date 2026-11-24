"""Read-only, bounded source inspection without exporting per-observation labels."""

import math
from collections import Counter


def h5ad_profile(path):
    import h5py
    import numpy as np

    def scalar(value):
        if isinstance(value, bytes):
            return value.decode('utf-8', 'replace')[:600]
        if hasattr(value, 'size') and value.size != 1:
            return [scalar(v) for v in value.tolist()]
        if hasattr(value, 'item'):
            value = value.item()
        return value if isinstance(value, (str, int, float, bool)) else str(value)[:600]

    def categories(node):
        if isinstance(node, h5py.Group) and 'categories' in node and 'codes' in node:
            names = [str(scalar(v)) for v in node['categories'][:]]
            codes = node['codes'][:]
            return names, codes
        if isinstance(node, h5py.Dataset) and len(node.shape) == 1 and len(node) <= 200000:
            values = [scalar(v) for v in node[:]]
            names = list(dict.fromkeys(str(v) for v in values))
            if len(names) <= 200:
                indices = {v: i for i, v in enumerate(names)}
                return names, np.array([indices[str(v)] for v in values])
        return None

    with h5py.File(path, 'r') as data:
        matrix = data.get('X')
        shape = matrix.attrs.get('shape', ()) if isinstance(matrix, h5py.Group) else getattr(matrix, 'shape', ())
        result = {'matrix_shape': list(map(int, shape)), 'obs_fields': list(data.get('obs', {}))[:80],
                  'var_fields': list(data.get('var', {}))[:80], 'obsm_fields': list(data.get('obsm', {}))[:40],
                  'raw_matrix_present': 'raw/X' in data, 'layer_names': list(data.get('layers', {}))[:40],
                  'matrix_semantics': 'Not inferred from integer-valued observations; use original documentation.',
                  'obs_summaries': {}, 'uns_metadata': {}, 'annotation_crosswalks': []}
        if matrix is not None:
            result['matrix_encoding'] = {str(k): scalar(v) for k, v in matrix.attrs.items()}
            values = matrix.get('data') if isinstance(matrix, h5py.Group) else matrix
            if isinstance(values, h5py.Dataset):
                if len(values.shape) == 1:
                    sample = values[:min(2000000, len(values))].reshape(-1)
                else:
                    columns = min(values.shape[1], 2000000)
                    rows = min(values.shape[0], max(1, 2000000 // max(1, columns)))
                    sample = values[:rows, :columns].reshape(-1)
                result['matrix_value_sample'] = {'dtype': str(values.dtype), 'stored_values': int(values.size),
                    'sample_size': int(sample.size), 'nonfinite': int((~np.isfinite(sample)).sum()),
                    'checked_entire_matrix': bool(sample.size == values.size),
                    'negative': int((sample < 0).sum()), 'noninteger': int((sample != np.floor(sample)).sum())}
        annotation = {}
        for name in result['obs_fields']:
            lowered = name.lower()
            if not any(v in lowered for v in ('type', 'annotation', 'sample', 'donor', 'batch', 'section', 'assay', 'tissue')):
                continue
            item = categories(data['obs'][name])
            if item is None:
                continue
            names, codes = item
            counts = Counter(int(v) for v in codes)
            summary = {'category_count': len(names), 'missing': int((codes < 0).sum()),
                       'category_sizes': sorted(counts.values(), reverse=True)[:40]}
            if any(v in lowered for v in ('cell_type', 'annotation', 'assay', 'tissue')):
                summary['categories'] = names[:80]
            result['obs_summaries'][name] = summary
            if any(v in lowered for v in ('cell_type', 'annotation')):
                annotation[name] = (names, codes)
        for first, (names, codes) in annotation.items():
            for second, (other_names, other_codes) in annotation.items():
                if first >= second or len(codes) != len(other_codes):
                    continue
                mapping = {}
                for left, right in zip(codes, other_codes):
                    if left >= 0 and right >= 0:
                        mapping.setdefault(int(left), set()).add(int(right))
                result['annotation_crosswalks'].append({'from': first, 'to': second,
                    'source_category_count': len(names), 'target_category_count': len(other_names),
                    'source_categories_mapping_to_multiple_targets': sum(len(v)>1 for v in mapping.values())})
        uns = data.get('uns')
        if isinstance(uns, h5py.Group):
            result['uns_keys'] = list(uns)[:80]
            for name in ('title', 'citation', 'schema_reference', 'schema_version', 'organism', 'organism_ontology_term_id', 'is_pre_analysis'):
                node = uns.get(name)
                if isinstance(node, h5py.Dataset) and node.size <= 1:
                    result['uns_metadata'][name] = scalar(node[()])
            spatial = uns.get('spatial')
            if isinstance(spatial, h5py.Group):
                result['spatial_library_count'] = sum(isinstance(v,h5py.Group) for v in spatial.values())
                single = spatial.get('is_single')
                if isinstance(single,h5py.Dataset) and single.size==1 and single.dtype.kind=='b':
                    result['spatial_is_single'] = bool(single[()])
        var = data.get('var')
        if isinstance(var, h5py.Group):
            key = scalar(var.attrs.get('_index', '_index'))
            node = var.get(key)
            if isinstance(node, h5py.Dataset):
                result['feature_identifier_examples'] = [scalar(v) for v in node[:5]]
            result['feature_summaries'] = {}
            for name in ('feature_biotype', 'feature_reference', 'feature_type'):
                node = var.get(name)
                if node is not None:
                    item = categories(node)
                    if item:
                        result['feature_summaries'][name] = {'categories': item[0][:15]}
        spatial = data.get('obsm/spatial')
        if isinstance(spatial, h5py.Dataset):
            result['spatial_coordinates'] = {'shape': list(spatial.shape), 'dtype': str(spatial.dtype),
                'all_finite': bool(np.isfinite(spatial[:]).all())}
        assays = result['obs_summaries'].get('assay', {}).get('categories', [])
        if result['uns_metadata'].get('schema_version') == '7.1.0' and assays == ['Slide-seqV2']:
            result['schema_matrix_requirement'] = {
                'source_url': 'https://raw.githubusercontent.com/chanzuckerberg/single-cell-curation/main/schema/7.1.0/schema.md',
                'version': '7.1.0', 'section': 'X (Matrix Layers), scRNA-seq UMI row',
                'rule': 'Slide-seqV2 requires deduplicated molecule counts, positive integers stored as float32; raw counts belong in raw.X unless no normalized matrix is provided, in which case they belong in X.',
                'interpretation': 'Schema-declared count semantics when raw.X is absent; numerical checks and the schema declaration are evidence, not independent validation of the experimental processing history.'}
        return result
