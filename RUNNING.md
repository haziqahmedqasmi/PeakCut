# Run PeakCut on Windows

Follow these steps to install and start PeakCut on your own computer. These instructions use PowerShell.

## 1. Install the requirements

Install Python 3.13 and Git if they are not already installed. Install FFmpeg from PowerShell:

```powershell
winget install Gyan.FFmpeg
```

Close and reopen PowerShell after installing FFmpeg, then check it is available:

```powershell
ffmpeg -version
```

Python 3.13 is used in the commands below. The project's pinned packages may not install with Python 3.14.

## 2. Get the project

If you already have a copy of PeakCut, skip to step 3 and use that folder. Otherwise, clone it into a permanent folder with a short path:

```powershell
git clone https://github.com/haziqahmedqasmi/PeakCut.git C:\peakcut
```

## 3. Install Python packages

Change to the folder containing PeakCut. For the example clone above:

```powershell
cd C:\peakcut
```

Install the project's packages:

```powershell
py -3.13 -m pip install -r requirements.txt
```

## 4. Start PeakCut

In the project folder, run:

```powershell
py -3.13 -m studio.server
```

Keep this PowerShell window open while you use PeakCut. The first start downloads the English speech model (about 480 MB), which can take a while.

## 5. Open the dashboard

In your browser, go to <http://127.0.0.1:8765>.

To use AI features, open **AI** in the sidebar, choose a provider, enter your API key, and select **Save and test**. You can also use PeakCut without a key; clip selection will use loudness instead.

## 6. Stop or start it again

To stop the server, focus its PowerShell window and press **Ctrl+C**. To start it again later, open PowerShell, change to your PeakCut folder, and run:

```powershell
cd C:\peakcut
py -3.13 -m studio.server
```

## Keep your credentials private

In the default local setup, PeakCut runs without sign-in and is intended for use on your own computer. Enter API keys in the app's settings, not in chat or a public file. Do not expose the server to other computers or the internet with sign-in off. See [DEPLOY.md](DEPLOY.md) before setting PeakCut up for other users.
