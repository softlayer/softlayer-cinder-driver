from distutils.core import setup

setup(
    name='SoftLayerOpenStack',
    version='0.1.1',
    author='Amol Jadhav',
    author_email='amol_jadhav@persistent.co.in',
    packages=[
        'slos',
        'slos.cinder',
        'slos.cinder.driver'],
    scripts=[],
    url='http://pypi.python.org/pypi/TowelStuff/',
    license='LICENSE.txt',
    description='SoftLayer Driver for OpenStack',
    long_description=open('README.txt').read(),
    install_requires=[
        "SoftLayer==3.0.1",
    ]
)
