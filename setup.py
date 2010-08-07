try:
    from setuptools import setup, find_packages
except ImportError:
    from ez_setup import use_setuptools
    use_setuptools()
    from setuptools import setup, find_packages

setup(
    name='hgbundler',
    version='1.0',
    description='Mercurial utilities for CPS distribution',
    author='Georges Racinet',
    author_email='georges@racinet.fr',
    url='',
    install_requires=['bundleman',
    ],
    packages=find_packages('src'),
    package_dir={'': 'src'},
    include_package_data=True,
    entry_points="""
    [console_scripts]
    hgbundler = hgbundler:main
    """
)
