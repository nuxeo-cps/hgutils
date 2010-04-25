try:
    from setuptools import setup, find_packages
except ImportError:
    from ez_setup import use_setuptools
    use_setuptools()
    from setuptools import setup, find_packages

setup(
    name='cpshgutils',
    version='0.1',
    description='Mercurial utilities for CPS distribution',
    author='Georges Racinet',
    author_email='georges@racinet.fr',
    url='',
    install_requires=['bundleman',
    ],
    packages=find_packages(exclude=['ez_setup']),
    include_package_data=True,
    entry_points="""
    [console_scripts]
    hgbundler = hgbundler:main
    """
)
