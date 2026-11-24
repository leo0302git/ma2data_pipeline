"""Inspect real AnnData encodings without inferring unsupported scientific units."""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from profiles import h5ad_profile

@unittest.skipUnless(importlib.util.find_spec('h5py'), 'h5py is optional on the authoring host')
class ProfileTests(unittest.TestCase):
    def test_array_attributes_categories_and_missing_annotations(self):
        import h5py
        import numpy as np
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'source.h5ad'
            with h5py.File(path,'w') as f:
                x=f.create_group('X');x.attrs['shape']=np.array([3,2]);x.attrs['encoding-type']='csr_matrix'
                x.create_dataset('data',data=np.array([1.,2.,3.],dtype='float32'))
                obs=f.create_group('obs')
                for name in ('cell_type','author_cell_type'):
                    cat=obs.create_group(name)
                    cat.create_dataset('categories',data=np.array(['a','b'],dtype=h5py.string_dtype()))
                    cat.create_dataset('codes',data=np.array([0,1,-1]))
                uns=f.create_group('uns');uns.create_dataset('title',data='one acquisition')
                uns.create_group('spatial').create_dataset('is_single',data=True)
            result=h5ad_profile(path)
            self.assertEqual(result['matrix_encoding']['shape'],[3,2])
            self.assertEqual(result['obs_summaries']['cell_type']['missing'],1)
            self.assertEqual(result['annotation_crosswalks'][0]['source_categories_mapping_to_multiple_targets'],0)
            self.assertEqual(result['spatial_library_count'],0)
            self.assertTrue(result['spatial_is_single'])
            self.assertIn('Not inferred',result['matrix_semantics'])

if __name__=='__main__':unittest.main()
