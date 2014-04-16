import sys

try:
    from setuptools import setup
except ImportError:
    print("Distribute is required for install:")
    print("    http://python-distribute.org/distribute_setup.py")
    sys.exit(1)

# Not supported for Python versions < 2.6
if sys.version_info <= (2, 6):
    print("Python 2.6 or greater is required.")
    sys.exit(1)

extra = {}
setup(
    name='SoftLayerOpenStack',
    version='0.2.1',
    author='Amol Jadhav',
    author_email='amol_jadhav@persistent.co.in',
    packages=[
        'slos',
        'slos.cinder',
        'slos.cinder.driver'],
    scripts=[],
#    url='http://pypi.python.org/pypi/TowelStuff/',
    license='LICENSE.txt',
    description='SoftLayer Driver for OpenStack',
    long_description=open('README.rst').read(),
    install_requires=[
        "SoftLayer==3.0.1",
    ],
    package_data={
    },
    test_suite='nose.collector',
    **extra

)
